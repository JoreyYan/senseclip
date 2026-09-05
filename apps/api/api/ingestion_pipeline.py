"""
完整摄入 Pipeline 编排器

流程（带缓存）：
  0. 检查 video_assets 表，已有音频/字幕直接跳过对应步骤
  1. 从 YouTube 下载音频（yt-dlp）
  2. 上传到 Supabase Storage → 写入 video_assets
  3. Groq Whisper 转录 → 写入 video_assets
  4. 调用 SupabasePipeline 处理（原子化 → 片段 → 实体 → 向量）
  5. 全程更新 pipeline_jobs 状态
"""

import asyncio
import hashlib
import logging
import re
import sys
import tempfile
from pathlib import Path

from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SUPABASE_URL, SUPABASE_KEY
from api.audio_service import AudioService
from api.groq_service import GroqService

SUBTITLES_BUCKET = "subtitles"

logger = logging.getLogger(__name__)


# ── 辅助函数 ────────────────────────────────────────────────

_STEP_ORDER = {
    "starting": 0,
    "audio_ready": 1,
    "transcribed": 2,
    "atomized": 3,
    "segmented": 4,
    "entities_extracted": 5,
    "topics_extracted": 6,
    "vectorized": 7,
    "processed": 8,
}

_PREVIOUS_STEP = {
    "atomized": "transcribed",
    "segmented": "atomized",
    "entities_extracted": "segmented",
    "topics_extracted": "entities_extracted",
    "vectorized": "topics_extracted",
    "processed": "vectorized",
}

_ASSET_STATUS_ORDER = {
    "pending": 0,
    "downloading": 0,
    "uploading": 0,
    "transcribing": 1,
    "audio_ready": 1,
    "transcribed": 2,
    "atomized": 3,
    "segmented": 4,
    "entities_extracted": 5,
    "topics_extracted": 6,
    "vectorized": 7,
    "processed": 8,
    "done": 8,
    "completed": 8,
    "error": -1,
}

_STEP_MESSAGE = {
    "starting": "开始内容分析...",
    "atomized": "原子化完成",
    "segmented": "片段化完成",
    "entities_extracted": "实体提取完成",
    "topics_extracted": "话题提取完成",
    "vectorized": "向量化完成",
    "processed": "分析完成",
}


def _step_rank(step: str | None) -> int:
    if not step:
        return 0
    return _STEP_ORDER.get(step, 0)


def _asset_status_rank(status: str | None) -> int:
    if not status:
        return 0
    return _ASSET_STATUS_ORDER.get(status, 0)


def _safe_ratio(done: object, total: object) -> tuple[int, int]:
    try:
        total_num = max(int(total), 0)
    except Exception:
        total_num = 0
    try:
        done_num = max(int(done), 0)
    except Exception:
        done_num = 0
    if total_num > 0:
        done_num = min(done_num, total_num)
    return done_num, total_num


def _format_pipeline_progress_message(step: str, payload: dict) -> str:
    if step == "atomizing_progress":
        done, total = _safe_ratio(payload.get("done_batches"), payload.get("total_batches"))
        if total > 0:
            return f"原子化进行中（批次 {done}/{total}）"
        return "原子化进行中..."

    if step == "entities_progress":
        phase = payload.get("phase")
        done, total = _safe_ratio(payload.get("done"), payload.get("total"))
        if phase == "annotating_atoms":
            return f"实体提取（原子注释）{done}/{total}" if total > 0 else "实体提取（原子注释）进行中..."
        if phase == "extracting_segments":
            return f"实体提取（片段抽取）{done}/{total}" if total > 0 else "实体提取（片段抽取）进行中..."
        return "实体提取进行中..."

    if step == "vectorizing_progress":
        done, total = _safe_ratio(payload.get("done"), payload.get("total"))
        if total > 0:
            return f"向量化 {done}/{total}"
        return "向量化进行中..."

    return _STEP_MESSAGE.get(step, step)


def _update_job(supabase, job_id: str, **kwargs) -> None:
    # Keep last_successful_step monotonic across retries.
    if "last_successful_step" in kwargs and kwargs.get("last_successful_step"):
        try:
            current = (
                supabase.table("pipeline_jobs")
                .select("last_successful_step")
                .eq("id", job_id)
                .limit(1)
                .execute()
            )
            current_step = current.data[0].get("last_successful_step") if current.data else None
            if _step_rank(current_step) > _step_rank(kwargs.get("last_successful_step")):
                kwargs["last_successful_step"] = current_step
        except Exception:
            pass

    try:
        supabase.table("pipeline_jobs").update(kwargs).eq("id", job_id).execute()
    except Exception:
        # Backward compatibility: some environments may not have these columns yet.
        if "last_successful_step" in kwargs or "failed_step" in kwargs:
            fallback = {
                k: v for k, v in kwargs.items()
                if k not in {"last_successful_step", "failed_step"}
            }
            if fallback:
                supabase.table("pipeline_jobs").update(fallback).eq("id", job_id).execute()
            return
        raise


def _extract_video_id(youtube_url: str) -> str:
    for pattern in [r"v=([^&]+)", r"youtu\.be/([^?&]+)", r"shorts/([^?&]+)"]:
        m = re.search(pattern, youtube_url)
        if m:
            return m.group(1)
    return hashlib.md5(youtube_url.encode()).hexdigest()[:11]


def _normalize_url(youtube_url: str) -> str:
    """标准化 YouTube URL，去掉时间戳等参数，只保留 video ID"""
    vid = _extract_video_id(youtube_url)
    if vid and not vid.startswith("手动上传"):
        return f"https://www.youtube.com/watch?v={vid}"
    return youtube_url


def _get_asset(supabase, youtube_url: str) -> dict | None:
    """查询 video_assets 表，返回已有资源记录"""
    canonical = _normalize_url(youtube_url)
    res = supabase.table("video_assets").select("*").eq("youtube_url", canonical).execute()
    return res.data[0] if res.data else None


def _upsert_asset(supabase, youtube_url: str, **kwargs) -> None:
    """更新或新建 video_assets 记录（用标准化 URL 作为 key）"""
    canonical = _normalize_url(youtube_url)
    incoming_status = kwargs.get("status")
    if incoming_status:
        try:
            existing = (
                supabase.table("video_assets")
                .select("status")
                .eq("youtube_url", canonical)
                .limit(1)
                .execute()
            )
            existing_status = existing.data[0].get("status") if existing.data else None
            if _asset_status_rank(existing_status) > _asset_status_rank(incoming_status):
                kwargs["status"] = existing_status
        except Exception:
            pass

    supabase.table("video_assets").upsert(
        {"youtube_url": canonical, **kwargs}
    ).execute()


def _video_has_persisted_analysis_assets(supabase, video_id: str) -> bool:
    """Return True when minimal persisted analysis assets exist for a video."""
    if not video_id:
        return False
    try:
        atoms_count = (
            supabase.table("atoms")
            .select("id", count="exact")
            .eq("video_id", video_id)
            .limit(1)
            .execute()
            .count
            or 0
        )
        segments_count = (
            supabase.table("narrative_segments")
            .select("id", count="exact")
            .eq("video_id", video_id)
            .limit(1)
            .execute()
            .count
            or 0
        )
        return atoms_count > 0 and segments_count > 0
    except Exception:
        return False


def _refresh_person_network_cache(supabase) -> None:
    """Best-effort rebuild of person network cache after pipeline success."""
    try:
        from api.person_network_service import PersonNetworkService

        service = PersonNetworkService(supabase)
        service.rebuild(limit_videos=300, use_ai=False, ai_model="claude-haiku-4-5")
    except Exception as e:
        logger.warning(f"person network refresh skipped: {e}")


def _extract_and_sync_relations(supabase, video_id: str) -> None:
    """Best-effort: extract directed person relations + sync persons table for the new video."""
    try:
        from api.person_relation_service import PersonRelationService
        from config import CLAUDE_API_KEY, TAVILY_API_KEY

        prs = PersonRelationService(supabase, CLAUDE_API_KEY, TAVILY_API_KEY)
        result = prs.extract_relations_for_videos([video_id], batch_size=12, model="claude-haiku-4-5")
        logger.info(
            f"[relation_extract] video={video_id} "
            f"relations={result.get('total_relations',0)} "
            f"career={result.get('total_career_events',0)}"
        )
    except Exception as e:
        logger.warning(f"relation extraction skipped for {video_id}: {e}")

    try:
        from api.person_network_service import PersonNetworkService
        from api.person_relation_service import PersonRelationService
        from config import CLAUDE_API_KEY, TAVILY_API_KEY

        network = PersonNetworkService(supabase).get_cached()
        if network:
            prs = PersonRelationService(supabase, CLAUDE_API_KEY, TAVILY_API_KEY)
            prs.sync_persons_from_cache(network)
    except Exception as e:
        logger.warning(f"persons sync skipped: {e}")


def _download_text(url: str, timeout: int = 30) -> str:
    """下载文本内容（用于 SRT）。"""
    import requests
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    # requests 会基于 headers 猜 encoding；这里尽量兜底
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def _upload_srt_to_storage(supabase, video_id: str, srt_text: str) -> str:
    """上传 SRT 到 Supabase Storage，并返回 public URL。"""
    storage_path = f"{video_id}.srt"
    supabase.storage.from_(SUBTITLES_BUCKET).upload(
        storage_path,
        srt_text.encode("utf-8"),
        {
            "content-type": "application/x-subrip; charset=utf-8",
            "upsert": "true",
        },
    )
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUBTITLES_BUCKET}/{storage_path}"


def _run_pipeline_sync(srt_path: Path, video_id: str, title: str, progress_callback=None) -> dict:
    """在线程池中同步运行 SupabasePipeline"""
    from supabase_pipeline import SupabasePipeline
    pipeline = SupabasePipeline()
    return pipeline.process_subtitle_file(
        srt_file_path=str(srt_path),
        video_id=video_id,
        video_title=title,
        progress_callback=progress_callback,
    )


# ── 主流程：从 YouTube URL ────────────────────────────────────

async def run_ingestion(job_id: str, youtube_url: str) -> None:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    audio_service = AudioService()
    groq_service = GroqService()
    loop = asyncio.get_event_loop()

    video_id = _extract_video_id(youtube_url)

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            asset = _get_asset(supabase, youtube_url)

            # ── Fast path: 已有字幕（Storage URL）但没有音频时，直接进入分析阶段 ──
            # 适配：你现在的历史数据是「subtitles bucket 已有 .srt，但 audio_url 为空」。
            if asset and asset.get("srt_content") and not asset.get("audio_url"):
                logger.info(f"[{job_id}] 已有字幕但无音频，跳过音频下载/转录，直接分析")
                audio_url = None
                _update_job(
                    supabase,
                    job_id,
                    status="processing",
                    current_step="检测到已存在字幕，跳过音频步骤，正在分析内容...",
                )
            else:
                # ── Step 1 & 2: 下载 + 上传音频（已有则跳过）──
                if asset and asset.get("audio_url"):
                    audio_url = asset["audio_url"]
                    logger.info(f"[{job_id}] 复用已有音频: {audio_url}")
                    _update_job(
                        supabase,
                        job_id,
                        status="transcribing",
                        current_step="复用已有音频，正在转录...",
                        audio_url=audio_url,
                    )
                else:
                    _update_job(
                        supabase,
                        job_id,
                        status="downloading",
                        current_step="正在从 YouTube 下载音频...",
                    )
                    local_path = await audio_service.download_audio(youtube_url, tmp_dir)

                    _update_job(
                        supabase,
                        job_id,
                        status="uploading",
                        current_step="正在上传音频到云端...",
                    )
                    audio_url = await audio_service.upload_to_storage(local_path, job_id)

                    _upsert_asset(
                        supabase,
                        youtube_url,
                        video_id=video_id,
                        audio_url=audio_url,
                        status="audio_ready",
                    )
                    _update_job(
                        supabase,
                        job_id,
                        status="transcribing",
                        current_step="正在转录音频为字幕...",
                        audio_url=audio_url,
                    )
            # ── Step 3: 转录（已有 SRT 则跳过）──
            # 说明：video_assets.srt_content 现在存的是 Storage URL（而不是正文）。
            if asset and asset.get("srt_content"):
                srt_ref = asset["srt_content"]
                logger.info(f"[{job_id}] 复用已有字幕: {srt_ref}")
                _update_job(supabase, job_id, status="processing",
                            current_step="复用已有字幕，正在分析内容...")

                # srt_content 可能是 URL 或旧数据（正文）。统一转为正文。
                if isinstance(srt_ref, str) and srt_ref.startswith("http"):
                    srt_text = await loop.run_in_executor(None, lambda: _download_text(srt_ref))
                else:
                    srt_text = srt_ref
            else:
                srt_text = await loop.run_in_executor(
                    None, lambda: groq_service.transcribe(audio_url)
                )

                # 上传到 Storage（subtitles bucket），并把 URL 写回 video_assets.srt_content
                srt_url = await loop.run_in_executor(
                    None, lambda: _upload_srt_to_storage(supabase, video_id, srt_text)
                )

                _upsert_asset(supabase, youtube_url,
                              srt_content=srt_url,
                              status="transcribed")

                # 更新 asset（后续步骤要用）
                asset = _get_asset(supabase, youtube_url)

                _update_job(supabase, job_id, status="processing",
                            current_step="正在分析视频内容...")

            # ── Step 4: SupabasePipeline（已处理则跳过）──
            # ⚠️ 注意：asset 可能是最早读取的版本（还没包含 srt_content/db_video_id 的最新值），
            # 所以这里再取一次最新记录，避免误判。
            asset_latest = _get_asset(supabase, youtube_url)
            if (
                asset_latest
                and asset_latest.get("db_video_id")
                and asset_latest.get("status") == "processed"
                and _video_has_persisted_analysis_assets(supabase, asset_latest.get("db_video_id"))
            ):
                db_video_id = asset_latest["db_video_id"]
                logger.info(f"[{job_id}] 已处理过，跳过 pipeline，video_id={db_video_id}")
            else:
                _upsert_asset(supabase, youtube_url, db_video_id=video_id)
                srt_path = Path(tmp_dir) / f"{video_id}.srt"
                srt_path.write_text(srt_text, encoding="utf-8")

                title = (asset.get("title") if asset else None) or f"YouTube: {video_id}"
                def _on_pipeline_progress(step: str, payload: dict):
                    step_message = _format_pipeline_progress_message(step, payload)
                    if step == "error":
                        failed_step = payload.get("failed_step", "unknown")
                        _update_job(
                            supabase,
                            job_id,
                            status="error",
                            current_step=f"处理失败（{failed_step}）",
                            error_message=str(payload.get("error_message", ""))[:500],
                            failed_step=failed_step,
                        )
                        _upsert_asset(
                            supabase,
                            youtube_url,
                            error_message=str(payload.get("error_message", ""))[:500],
                        )
                        return
                    if step in {"atomizing_progress", "entities_progress", "vectorizing_progress"}:
                        _update_job(
                            supabase,
                            job_id,
                            status="processing",
                            current_step=step_message,
                            failed_step=None,
                            error_message=None,
                        )
                        return

                    _update_job(
                        supabase,
                        job_id,
                        status="processing",
                        current_step=step_message,
                        last_successful_step=step if step != "starting" else None,
                        failed_step=None,
                        error_message=None,
                    )
                    if step != "starting":
                        _upsert_asset(supabase, youtube_url, status=step)

                result = await loop.run_in_executor(
                    None, lambda: _run_pipeline_sync(
                        srt_path, video_id, title, progress_callback=_on_pipeline_progress
                    )
                )
                db_video_id = result.get("video_id", video_id)
                _upsert_asset(supabase, youtube_url,
                              db_video_id=db_video_id,
                              status="processed")

                # 更新 asset（让后续依赖字段一致）
                asset = _get_asset(supabase, youtube_url)

            _update_job(supabase, job_id, status="completed",
                        current_step="完成", video_id=db_video_id,
                        last_successful_step="processed", failed_step=None, error_message=None)
            _refresh_person_network_cache(supabase)
            _extract_and_sync_relations(supabase, db_video_id)
            logger.info(f"[{job_id}] 完成，video_id={db_video_id}")

        except Exception as e:
            logger.error(f"[{job_id}] 失败: {e}", exc_info=True)
            _update_job(supabase, job_id, status="error",
                        current_step="处理失败", error_message=str(e)[:500], failed_step="unknown")
            _upsert_asset(supabase, youtube_url,
                          error_message=str(e)[:500])


# ── 备用流程：手动上传音频 ────────────────────────────────────

async def run_ingestion_from_bytes(job_id: str, audio_bytes: bytes, filename: str) -> None:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    groq_service = GroqService()
    audio_service = AudioService()
    loop = asyncio.get_event_loop()

    video_id = hashlib.md5(filename.encode()).hexdigest()[:11]
    fake_url = f"[手动上传] {filename}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            # 上传 Storage
            _update_job(supabase, job_id, status="uploading",
                        current_step="正在上传音频到云端...")
            local_path = Path(tmp_dir) / filename
            local_path.write_bytes(audio_bytes)
            audio_url = await audio_service.upload_to_storage(local_path, job_id)
            _upsert_asset(supabase, fake_url,
                          video_id=video_id, audio_url=audio_url, status="audio_ready")
            _update_job(supabase, job_id, audio_url=audio_url)

            # 转录
            _update_job(supabase, job_id, status="transcribing",
                        current_step="正在转录音频为字幕...")
            srt_content = await loop.run_in_executor(
                None, lambda: groq_service.transcribe(audio_url)
            )
            _upsert_asset(supabase, fake_url,
                          srt_content=srt_content, status="transcribed")

            # Pipeline
            _update_job(supabase, job_id, status="processing",
                        current_step="正在分析视频内容...")
            srt_path = Path(tmp_dir) / f"{video_id}.srt"
            srt_path.write_text(srt_content, encoding="utf-8")
            _upsert_asset(supabase, fake_url, db_video_id=video_id)

            def _on_pipeline_progress(step: str, payload: dict):
                step_message = _format_pipeline_progress_message(step, payload)
                if step == "error":
                    failed_step = payload.get("failed_step", "unknown")
                    _update_job(
                        supabase,
                        job_id,
                        status="error",
                        current_step=f"处理失败（{failed_step}）",
                        error_message=str(payload.get("error_message", ""))[:500],
                        failed_step=failed_step,
                    )
                    _upsert_asset(
                        supabase, fake_url,
                        error_message=str(payload.get("error_message", ""))[:500]
                    )
                    return
                if step in {"atomizing_progress", "entities_progress", "vectorizing_progress"}:
                    _update_job(
                        supabase,
                        job_id,
                        status="processing",
                        current_step=step_message,
                        failed_step=None,
                        error_message=None,
                    )
                    return

                _update_job(
                    supabase,
                    job_id,
                    status="processing",
                    current_step=step_message,
                    last_successful_step=step if step != "starting" else None,
                    failed_step=None,
                    error_message=None,
                )
                if step != "starting":
                    _upsert_asset(supabase, fake_url, status=step)

            result = await loop.run_in_executor(
                None, lambda: _run_pipeline_sync(
                    srt_path, video_id, filename, progress_callback=_on_pipeline_progress
                )
            )
            db_video_id = result.get("video_id", video_id)
            _upsert_asset(supabase, fake_url,
                          db_video_id=db_video_id, status="processed")

            _update_job(supabase, job_id, status="completed",
                        current_step="完成", video_id=db_video_id,
                        last_successful_step="processed", failed_step=None, error_message=None)
            _refresh_person_network_cache(supabase)
            _extract_and_sync_relations(supabase, db_video_id)
            logger.info(f"[{job_id}] 手动上传完成，video_id={db_video_id}")

        except Exception as e:
            logger.error(f"[{job_id}] 失败: {e}", exc_info=True)
            _update_job(supabase, job_id, status="error",
                        current_step="处理失败", error_message=str(e)[:500], failed_step="unknown")
            _upsert_asset(supabase, fake_url,
                          error_message=str(e)[:500])


async def run_reprocess(job_id: str, video_id: str, srt_ref: str, video_title: str) -> None:
    """强制重新处理：下载 SRT → 重新原子化（force_reprocess=True）"""
    import urllib.request as _urllib
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    loop = asyncio.get_event_loop()

    def _on_pipeline_progress(event: str, payload: dict):
        _update_job(supabase, job_id, current_step=f"[重处理] {event}")

    try:
        _update_job(supabase, job_id, status="processing", current_step="下载 SRT...")

        with tempfile.TemporaryDirectory() as tmp_dir:
            srt_path = Path(tmp_dir) / f"{video_id}.srt"

            # srt_ref is either a URL or raw content
            if srt_ref.startswith("http"):
                srt_bytes = _urllib.urlopen(srt_ref).read()
                srt_path.write_bytes(srt_bytes)
            else:
                srt_path.write_text(srt_ref, encoding="utf-8")

            _update_job(supabase, job_id, current_step="重新原子化中（force_reprocess）...")

            sys.path.insert(0, str(Path(__file__).parent.parent))
            from supabase_pipeline import SupabasePipeline

            pipeline = SupabasePipeline()

            result = await loop.run_in_executor(
                None,
                lambda: pipeline.process_subtitle_file(
                    str(srt_path), video_id=video_id, video_title=video_title,
                    progress_callback=_on_pipeline_progress,
                    force_reprocess=True
                )
            )

            db_video_id = result.get("video_id", video_id)
            _update_job(supabase, job_id, status="completed",
                        current_step="重新处理完成", video_id=db_video_id,
                        last_successful_step="processed", failed_step=None, error_message=None)
            _refresh_person_network_cache(supabase)
            _extract_and_sync_relations(supabase, db_video_id)
            logger.info(f"[{job_id}] 重新处理完成，video_id={db_video_id}")

    except Exception as e:
        logger.error(f"[{job_id}] 重新处理失败: {e}", exc_info=True)
        _update_job(supabase, job_id, status="error",
                    current_step="重新处理失败", error_message=str(e)[:500], failed_step="reprocess")
