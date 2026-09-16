"""
服务器端自动补齐 worker

- 常驻后台线程：扫频道（videos/streams/shorts）→ 找出未完成视频 → 逐个跑完整 pipeline
- 状态持久化在 app_settings（backfill_worker_v1），容器重启后自动恢复
- backlog 清空后进入巡航模式：每 RESCAN_MINUTES 分钟重扫一次，频道新视频自动处理
- 不再依赖操作者本机开机/联网
"""

import asyncio
import concurrent.futures as cf
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_KEY = "backfill_worker_v1"
FILTER_CACHE_KEY = "backfill_topic_filter_v1"  # {channel: {video_id: 1/0}}
RESCAN_MINUTES = int(os.environ.get('BACKFILL_RESCAN_MINUTES', '20'))  # 巡航重扫间隔(勤扫也让 cookies 保持活跃)
PER_VIDEO_TIMEOUT = 3600  # 单视频硬超时（秒）
CONCURRENCY = max(1, int(os.environ.get("BACKFILL_CONCURRENCY", "3")))  # 并发视频数
# 超长视频(直播回放等)跳过阈值:处理成本高、信息密度低。0 = 不限制
MAX_SECONDS = int(os.environ.get("BACKFILL_MAX_SECONDS", "5400"))


def _is_cookie_error(err: str) -> bool:
    e = (err or "").lower()
    return ("cookies are no longer valid" in e or "sign in to confirm" in e
            or "confirm you're not a bot" in e or "confirm you’re not a bot" in e)


class BackfillWorker:
    def __init__(self, supabase):
        self.supabase = supabase
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status: dict = {
            "running": False, "phase": "idle", "channel": "",
            "channel_total": 0, "missing": 0,
            "done_session": 0, "failed_session": 0,
            "current": "", "last_scan": "", "last_error": "",
        }
        # 视频级失败退避:{video_id: (连续失败次数, 下次允许重试的时间戳)}
        self._fail: dict = {}

    # ── 状态持久化(多频道)────────────────────────────────────
    def _save_state(self, enabled: bool, channels: list) -> None:
        try:
            self.supabase.table("app_settings").upsert({
                "key": STATE_KEY,
                "value": json.dumps({"enabled": enabled, "channels": channels}),
            }).execute()
        except Exception as e:
            logger.warning(f"[backfill] save state failed: {e}")

    def load_state(self) -> dict:
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", STATE_KEY).execute()
            if r.data:
                state = json.loads(r.data[0]["value"]) or {}
                # 兼容旧格式 {channel_url: ...} → channels 列表
                if "channels" not in state and state.get("channel_url"):
                    url = state["channel_url"]
                    state["channels"] = [{"url": url, "name": self._name_from_url(url)}]
                return state
        except Exception as e:
            logger.warning(f"[backfill] load state failed: {e}")
        return {}

    @staticmethod
    def _name_from_url(url: str) -> str:
        # https://www.youtube.com/@zrzjpl → zrzjpl
        part = url.rstrip("/").split("/")[-1]
        return part.lstrip("@") or "unknown"

    # ── 控制 ──────────────────────────────────────────────────
    def start(self, channel_url: str) -> dict:
        """启动 worker;传入的频道会并入频道列表(已存在则不重复)。"""
        state = self.load_state()
        channels = state.get("channels") or []
        url = channel_url.rstrip("/")
        if url and not any(c["url"].rstrip("/") == url for c in channels):
            channels.append({"url": url, "name": self._name_from_url(url)})
        if not channels:
            return {"status": "no_channels"}
        self._save_state(True, channels)
        self.status["channel"] = ", ".join(c["name"] for c in channels)
        if self._thread and self._thread.is_alive():
            return {"status": "already_running(channel added, next scan picks it up)", **self.status}
        self._stop.clear()
        self.status.update({"running": True,
                            "done_session": 0, "failed_session": 0, "last_error": ""})
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[backfill] worker started, channels: {self.status['channel']}")
        return {"status": "started", **self.status}

    def stop(self) -> dict:
        self._stop.set()
        state = self.load_state()
        self._save_state(False, state.get("channels") or [])
        self.status["running"] = False
        self.status["phase"] = "stopping"
        return {"status": "stopping"}

    # ── 频道扫描 ──────────────────────────────────────────────
    def _scan_channel(self, channel_url: str, max_seconds: int = None,
                      topic_filter: str = "", channel_name: str = "") -> list:
        """扫频道三个 tab。max_seconds 为频道级时长上限(None 用全局);
        topic_filter 非空时按标题用大模型判断是否属于要收录的主题(结果缓存)。"""
        base = channel_url.rstrip("/")
        limit = MAX_SECONDS if max_seconds is None else int(max_seconds)
        titles: dict = {}
        cookies_text = ""
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", "youtube_cookies").execute()
            if r.data:
                cookies_text = r.data[0]["value"] or ""
        except Exception:
            pass

        ids: list = []
        skipped_long = 0
        with tempfile.TemporaryDirectory() as tmp:
            cmd_base = ["yt-dlp", "--flat-playlist", "--print", "%(id)s|%(duration)s|%(title)s",
                        "--extractor-args", "youtubetab:skip=authcheck"]
            if cookies_text:
                ck = Path(tmp) / "cookies.txt"
                ck.write_text(cookies_text, encoding="utf-8")
                cmd_base += ["--cookies", str(ck)]
            for tab in ("videos", "streams", "shorts"):
                try:
                    result = subprocess.run(
                        cmd_base + [f"{base}/{tab}"],
                        capture_output=True, text=True, timeout=300,
                        encoding="utf-8", errors="replace",
                    )
                    tab_ids = []
                    for l in (result.stdout or "").splitlines():
                        parts = l.strip().split("|", 2)
                        vid = parts[0].strip()
                        if not vid or len(vid) != 11:
                            continue
                        dur = 0.0
                        if len(parts) > 1:
                            try:
                                dur = float(parts[1])
                            except (ValueError, TypeError):
                                dur = 0.0
                        if len(parts) > 2:
                            titles[vid] = parts[2].strip()
                        if limit and dur > limit:
                            skipped_long += 1
                            continue
                        tab_ids.append(vid)
                    logger.info(f"[backfill] scan {tab}: {len(tab_ids)}")
                    ids.extend(tab_ids)
                except Exception as e:
                    logger.warning(f"[backfill] scan {tab} failed: {e}")
        if skipped_long:
            logger.info(f"[backfill] skipped {skipped_long} videos over {limit}s")
            self.status["skipped_long"] = self.status.get("skipped_long", 0) + skipped_long
        ids = list(dict.fromkeys(ids))
        if topic_filter and ids:
            keep = self._apply_topic_filter(channel_name or self._name_from_url(base),
                                            topic_filter, ids, titles)
            logger.info(f"[backfill] topic filter {channel_name}: {len(keep)}/{len(ids)} kept")
            self.status.setdefault("filtered", {})[channel_name] = f"{len(keep)}/{len(ids)}"
            ids = keep
        return ids

    # ── 主题过滤(按标题,大模型判断,结果缓存在 app_settings)──────────
    def _apply_topic_filter(self, channel: str, criteria: str, ids: list, titles: dict) -> list:
        cache_key = FILTER_CACHE_KEY
        cache: dict = {}
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", cache_key).execute()
            if r.data:
                cache = json.loads(r.data[0]["value"]) or {}
        except Exception as e:
            logger.warning(f"[backfill] filter cache load failed: {e}")
        ch_cache: dict = cache.get(channel) or {}
        todo = [v for v in ids if v not in ch_cache and titles.get(v)]
        if todo:
            try:
                import openai as _oa
                from config import DEEPSEEK_API_KEY
                client = _oa.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
            except Exception as e:
                logger.warning(f"[backfill] filter client init failed: {e}")
                client = None
            for i in range(0, len(todo), 30) if client else []:
                batch = todo[i:i + 30]
                listing = "\n".join(f"{n + 1}. {titles[v][:120]}" for n, v in enumerate(batch))
                prompt = (
                    f"下面是一个 YouTube 频道的视频标题。收录标准:\n{criteria}\n\n"
                    f"逐条判断是否收录。只输出 JSON:{{\"keep\": [要收录的序号]}}\n\n{listing}")
                try:
                    resp = client.chat.completions.create(
                        model="deepseek-v4-flash", max_tokens=1000, timeout=120,
                        messages=[{"role": "user", "content": prompt}],
                        extra_body={"thinking": {"type": "disabled"}})  # 推理模式会把 token 耗光返回空
                    import re as _re
                    m = _re.search(r"\{.*\}", resp.choices[0].message.content or "", _re.S)
                    if not m:
                        raise ValueError("no json in filter response")
                    keep_idx = set(int(x) for x in (json.loads(m.group(0)).get("keep") or []))
                except Exception as e:
                    logger.warning(f"[backfill] filter batch failed: {str(e)[:100]}")
                    continue          # 失败的这批不写缓存,下轮再判
                for n, v in enumerate(batch):
                    ch_cache[v] = 1 if (n + 1) in keep_idx else 0
                cache[channel] = ch_cache
                try:
                    self.supabase.table("app_settings").upsert(
                        {"key": cache_key, "value": json.dumps(cache, ensure_ascii=False)}).execute()
                except Exception as e:
                    logger.warning(f"[backfill] filter cache save failed: {e}")
        # 没有标题/尚未判定的视频先不收(下一轮再判),避免把不相关内容吞进库
        return [v for v in ids if ch_cache.get(v) == 1]

    # ── 完成度 ────────────────────────────────────────────────
    def _get_done_set(self, channel_ids: list) -> set:
        done: set = set()
        try:
            rows = self.supabase.rpc("get_video_stats", {"p_video_ids": channel_ids}).execute().data or []
            for r in rows:
                if (r.get("atom_count") or 0) > 0 and (r.get("embedding_count") or 0) > 0:
                    done.add(r["video_id"])
        except Exception as e:
            logger.warning(f"[backfill] get_video_stats failed: {e}")
        return done

    # ── 单视频处理 ────────────────────────────────────────────
    def _process_one(self, video_id: str, channel_name: str = "") -> str:
        from api.ingestion_pipeline import run_ingestion
        url = f"https://www.youtube.com/watch?v={video_id}"
        job_id = str(uuid.uuid4())
        try:
            self.supabase.table("pipeline_jobs").insert({
                "id": job_id, "youtube_url": url,
                "status": "pending", "current_step": "[worker] 排队处理...",
            }).execute()
        except Exception as e:
            logger.warning(f"[backfill] job insert failed for {video_id}: {e}")
            return "submit_failed"
        self._prune_jobs(url)

        async def _run():
            await asyncio.wait_for(run_ingestion(job_id, url), timeout=PER_VIDEO_TIMEOUT)

        try:
            asyncio.run(_run())
        except asyncio.TimeoutError:
            return "timeout"
        except Exception as e:
            logger.warning(f"[backfill] {video_id} failed: {str(e)[:120]}")
            return "error"

        # 打上内容归属频道标记(区分不同博主的库)
        if channel_name:
            try:
                self.supabase.table("videos").update(
                    {"channel": channel_name}).eq("id", video_id).execute()
            except Exception as e:
                logger.warning(f"[backfill] channel tag failed for {video_id}: {e}")

        # 以 job 最终状态为准
        try:
            r = self.supabase.table("pipeline_jobs").select("status,error_message").eq("id", job_id).execute()
            if r.data and r.data[0].get("status") == "completed":
                return "completed"
            err = (r.data[0].get("error_message") or "") if r.data else ""
            if "Insufficient Balance" in err or "402" in err:
                return "no_balance"
            low = err.lower()
            if "members-only" in low or "channel's members" in low:
                return "members_only"
            if "live event will begin" in low or "premieres in" in low or "premiere will begin" in low:
                return "upcoming"
            # yt-dlp 几乎每次都会顺带打印 "cookies are no longer valid" 的 WARNING,不能据此判断;
            # 只有出现机器人验证/要求登录时才算 cookies 问题
            if "sign in to confirm" in low or "not a bot" in low:
                return "cookies_invalid"
            return "error"
        except Exception:
            return "unknown"

    def _prune_jobs(self, url: str, keep: int = 3) -> None:
        """同一视频只保留最近几条 job,防止重试把表撑爆。"""
        try:
            rows = (self.supabase.table("pipeline_jobs").select("id")
                    .eq("youtube_url", url).order("created_at", desc=True)
                    .limit(200).execute().data) or []
            stale = [r["id"] for r in rows[keep:]]
            if stale:
                self.supabase.table("pipeline_jobs").delete().in_("id", stale).execute()
        except Exception as e:
            logger.warning(f"[backfill] prune failed for {url}: {str(e)[:80]}")

    def _cookies_version(self) -> str:
        try:
            r = (self.supabase.table("app_settings").select("updated_at")
                 .eq("key", "youtube_cookies").execute())
            return (r.data[0].get("updated_at") or "") if r.data else ""
        except Exception:
            return ""

    def _wait_or_cookies_change(self, seconds: int, cookies_ver: str) -> bool:
        """等待 seconds;期间若用户重新上传 cookies 立即返回。返回 True 表示收到停止信号。"""
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.wait(min(60, max(1, end - time.time()))):
                return True
            if cookies_ver and self._cookies_version() != cookies_ver:
                logger.info("[backfill] cookies updated — resuming now")
                self._fail.clear()
                return False
        return False

    def _backoff(self, vid: str) -> None:
        n = self._fail.get(vid, (0, 0))[0] + 1
        delay = min(RESCAN_MINUTES * 60 * (2 ** (n - 1)), 24 * 3600)
        self._fail[vid] = (n, time.time() + delay)

    # ── 主循环(多频道)───────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                state = self.load_state()
                channels = state.get("channels") or []
                self.status.update({"running": True, "phase": "scanning",
                                    "channel": ", ".join(c["name"] for c in channels)})

                # 扫全部频道,记录每个视频属于哪个频道
                self.status["skipped_long"] = 0
                vid_channel: dict = {}
                for ch in channels:
                    ids = self._scan_channel(ch["url"], ch.get("max_seconds"),
                                             ch.get("filter") or "", ch["name"])
                    logger.info(f"[backfill] {ch['name']}: {len(ids)} videos")
                    for v in ids:
                        vid_channel.setdefault(v, ch["name"])
                channel_ids = list(vid_channel.keys())
                self.status["channel_total"] = len(channel_ids)
                self.status["last_scan"] = datetime.now(timezone.utc).isoformat()

                if not channel_ids:
                    self.status["last_error"] = "channel scan returned 0 videos (cookies expired?)"
                    logger.warning("[backfill] scan empty — sleeping before retry")
                    if self._stop.wait(RESCAN_MINUTES * 60):
                        break
                    continue

                done = self._get_done_set(channel_ids)
                now_ts = time.time()
                for v in list(self._fail):
                    if v in done:
                        self._fail.pop(v, None)
                missing = [v for v in channel_ids
                           if v not in done and self._fail.get(v, (0, 0))[1] <= now_ts]
                backing_off = sum(1 for v in channel_ids
                                  if v not in done and v not in missing)
                self.status["backing_off"] = backing_off
                self.status["missing"] = len(missing)
                logger.info(f"[backfill] {len(done)}/{len(channel_ids)} done, {len(missing)} missing")

                if not missing:
                    self.status["phase"] = (f"idle (rescan in {RESCAN_MINUTES}min"
                                            + (f", {backing_off} 个失败视频退避中" if backing_off else "") + ")")
                    if self._stop.wait(RESCAN_MINUTES * 60):
                        break
                    continue

                # 并发处理:滑动窗口,同时最多 CONCURRENCY 个视频在流水线中
                self.status["phase"] = f"processing (x{CONCURRENCY})"
                queue = list(missing)
                in_flight: dict = {}
                round_failed = 0
                cookies_bad = False
                cookies_ver = self._cookies_version()
                with cf.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                    while (queue or in_flight) and not self._stop.is_set():
                        while queue and len(in_flight) < CONCURRENCY:
                            vid = queue.pop(0)
                            fut = pool.submit(self._process_one, vid, vid_channel.get(vid, ""))
                            in_flight[fut] = vid
                            self.status["current"] = ", ".join(in_flight.values())
                        done_set, _ = cf.wait(list(in_flight), timeout=30,
                                              return_when=cf.FIRST_COMPLETED)
                        balance_pause = False
                        for fut in done_set:
                            vid = in_flight.pop(fut)
                            try:
                                outcome = fut.result()
                            except Exception as ex:
                                outcome = f"error: {str(ex)[:60]}"
                            if outcome == "completed":
                                self.status["done_session"] += 1
                                self.status["missing"] = max(0, self.status["missing"] - 1)
                            elif outcome == "members_only":
                                # 会员专享:没有会员账号永远下不了,7 天后再看一眼(可能转公开)
                                self._fail[vid] = (99, time.time() + 7 * 24 * 3600)
                                self.status["missing"] = max(0, self.status["missing"] - 1)
                                self.status["skipped_members"] = self.status.get("skipped_members", 0) + 1
                            elif outcome == "upcoming":
                                # 预约直播/首映还没开始:正常退避,开播结束后自然能下
                                self._backoff(vid)
                                self.status["missing"] = max(0, self.status["missing"] - 1)
                            elif outcome == "cookies_invalid":
                                # cookies 失效:部分视频无 cookies 仍可下,只退避这一个并提示重新上传
                                self._backoff(vid)
                                round_failed += 1
                                cookies_bad = True
                                self.status["failed_session"] += 1
                                self.status["missing"] = max(0, self.status["missing"] - 1)
                                self.status["last_error"] = "YouTube cookies 已失效,请在 /settings 重新上传"
                            elif outcome == "no_balance":
                                # DeepSeek 没钱:视频回队列,稍后熔断暂停,避免空转刷失败
                                queue.append(vid)
                                balance_pause = True
                            else:
                                self._backoff(vid)
                                round_failed += 1
                                self.status["failed_session"] += 1
                                self.status["last_error"] = f"{vid}: {outcome}"
                                self.status["missing"] = max(0, self.status["missing"] - 1)
                        if balance_pause:
                            # 在飞任务继续跑完,但暂停投放新任务 30 分钟
                            self.status["phase"] = "paused 30min (DeepSeek 余额不足,充值后自动恢复)"
                            logger.warning("[backfill] DeepSeek insufficient balance — pausing 30min")
                            if self._stop.wait(1800):
                                break
                            self.status["phase"] = f"processing (x{CONCURRENCY})"
                        self.status["current"] = ", ".join(in_flight.values())
                # 一轮跑完:有失败就先歇一个巡航周期再重扫(旧逻辑立刻重扫,
                # 失败视频每 20 秒重下一次,一天刷上万条错误 job,还会加速 cookies 被 YouTube 作废)
                if round_failed:
                    self.status["phase"] = (f"waiting {RESCAN_MINUTES}min ({round_failed} failed this round"
                                            + (", YouTube cookies 已失效,请在 /settings 重新上传" if cookies_bad else "") + ")")
                    if cookies_bad:
                        logger.warning("[backfill] YouTube cookies invalid — re-upload in /settings")
                    if self._wait_or_cookies_change(RESCAN_MINUTES * 60, cookies_ver):
                        break
            except Exception as e:
                self.status["last_error"] = str(e)[:200]
                logger.error(f"[backfill] loop error: {e}")
                if self._stop.wait(600):
                    break

        self.status.update({"running": False, "phase": "stopped", "current": ""})
        logger.info("[backfill] worker stopped")
