"""
Groq Whisper 转录服务
- 优先 URL 直传（快，但 Groq 当前限制约 25MB）
- 超限时自动降级：下载音频 → ffmpeg 压缩切分为 20 分钟小段 → 分段转录 → 按时间偏移合并
"""

import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GROQ_API_KEY

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 1200          # 20 min per chunk
GROQ_SIZE_LIMIT = 24 * 1024 * 1024  # stay under Groq's ~25MB cap


def _segments_to_srt(segments: list) -> str:
    """将 Groq verbose_json 的 segments 转换为 SRT 格式"""
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _seconds_to_srt_time(seg["start"])
        end = _seconds_to_srt_time(seg["end"])
        text = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _seconds_to_srt_time(seconds: float) -> str:
    """秒数转 SRT 时间格式 HH:MM:SS,mmm"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds // 60) % 60
    h = int(seconds // 3600)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ffmpeg_duration(path: str) -> float:
    """探测音频时长（秒）"""
    result = subprocess.run(
        ["ffmpeg", "-i", path, "-hide_banner"],
        capture_output=True, text=True,
    )
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            t = line.split("Duration:")[1].split(",")[0].strip()
            p = t.split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    return 0.0


class GroqService:
    def __init__(self):
        from groq import Groq
        self.client = Groq(api_key=GROQ_API_KEY)

    def transcribe(self, audio_url: str, language: str = None) -> str:
        """转录音频为 SRT。优先 URL 直传；文件过大时自动切分转录。
        language=None 时 Whisper 自动检测语言（支持中英文频道混跑）。"""
        logger.info(f"开始转录: {audio_url}")
        try:
            kwargs = dict(
                model="whisper-large-v3-turbo",
                url=audio_url,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
            if language:
                kwargs["language"] = language
            transcription = self.client.audio.transcriptions.create(**kwargs)
            segments = transcription.segments or []
            if not segments:
                logger.warning("转录结果无分段，使用完整文本")
                return f"1\n00:00:00,000 --> 00:00:01,000\n{transcription.text}\n"
            srt = _segments_to_srt(segments)
            logger.info(f"转录完成，共 {len(segments)} 个片段")
            return srt
        except Exception as e:
            msg = str(e)
            if "too large" not in msg and "413" not in msg:
                raise
            logger.warning(f"URL 直传超过 Groq 大小限制，降级为切分转录: {msg[:120]}")
            return self._transcribe_chunked(audio_url, language)

    # ── 切分转录 ────────────────────────────────────────────────

    def _transcribe_chunked(self, audio_url: str, language: str) -> str:
        import httpx

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = os.path.join(tmpdir, "audio_raw")
            logger.info("下载音频用于切分...")
            with httpx.stream("GET", audio_url, timeout=600, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(raw_path, "wb") as f:
                    for chunk in resp.iter_bytes(1024 * 256):
                        f.write(chunk)
            size_mb = os.path.getsize(raw_path) / 1024 / 1024
            duration = _ffmpeg_duration(raw_path)
            n_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
            logger.info(f"音频 {size_mb:.1f}MB / {duration/60:.0f}min，切成 {n_chunks} 段")

            all_segments: list = []
            for i in range(n_chunks):
                start = i * CHUNK_SECONDS
                chunk_path = os.path.join(tmpdir, f"chunk{i:03d}.mp3")
                # 压缩为 mono 16k 48kbps：20 分钟 ≈ 7MB，远低于限制
                subprocess.run(
                    ["ffmpeg", "-y", "-i", raw_path,
                     "-ss", str(start), "-t", str(CHUNK_SECONDS),
                     "-ac", "1", "-ar", "16000", "-b:a", "48k",
                     chunk_path],
                    capture_output=True, timeout=600, check=True,
                )
                if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 1000:
                    logger.warning(f"分段 {i} 为空，跳过（可能已到末尾）")
                    continue

                logger.info(f"转录分段 {i+1}/{n_chunks} "
                            f"({os.path.getsize(chunk_path)/1024/1024:.1f}MB)...")
                last_error = None
                for attempt in range(3):
                    try:
                        ckwargs = dict(
                            model="whisper-large-v3-turbo",
                            response_format="verbose_json",
                            timestamp_granularities=["segment"],
                        )
                        if language:
                            ckwargs["language"] = language
                        with open(chunk_path, "rb") as f:
                            tr = self.client.audio.transcriptions.create(
                                file=(f"chunk{i:03d}.mp3", f), **ckwargs)
                        for seg in (tr.segments or []):
                            all_segments.append({
                                "start": float(seg["start"]) + start,
                                "end": float(seg["end"]) + start,
                                "text": seg["text"],
                            })
                        last_error = None
                        break
                    except Exception as ce:
                        last_error = ce
                        logger.warning(f"分段 {i} 转录失败（尝试 {attempt+1}）: {str(ce)[:100]}")
                        import time as _t
                        _t.sleep(15)
                if last_error is not None:
                    raise last_error

            if not all_segments:
                raise RuntimeError("切分转录后没有得到任何字幕分段")
            srt = _segments_to_srt(all_segments)
            logger.info(f"切分转录完成，共 {len(all_segments)} 个片段")
            return srt
