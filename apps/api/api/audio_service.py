"""
音频下载和存储服务
- 用 yt-dlp 从 YouTube 下载音频
- 压缩为 64kbps 单声道 mp3（~30MB/小时）
- 上传到 Supabase Storage public bucket
- 管理 cookies（从 Supabase app_settings 表读取）
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from supabase import create_client

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SUPABASE_URL, SUPABASE_KEY, AUDIO_BUCKET

logger = logging.getLogger(__name__)


class AudioService:
    def __init__(self):
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    async def get_cookies(self) -> str | None:
        """从 Supabase app_settings 表读取 YouTube cookies 内容"""
        try:
            result = self.supabase.table("app_settings").select("value").eq("key", "youtube_cookies").execute()
            if result.data:
                return result.data[0]["value"]
        except Exception as e:
            logger.warning(f"获取 cookies 失败: {e}")
        return None

    async def save_cookies(self, cookies_content: str) -> None:
        """保存 cookies 到 Supabase app_settings 表"""
        self.supabase.table("app_settings").upsert({
            "key": "youtube_cookies",
            "value": cookies_content
        }).execute()
        logger.info("cookies 已更新")

    async def download_audio(self, youtube_url: str, tmp_dir: str) -> Path:
        """
        用 yt-dlp 下载音频并压缩为 64kbps 单声道 mp3
        返回本地临时文件路径
        """
        cookies = await self.get_cookies()
        output_path = Path(tmp_dir) / "audio.%(ext)s"
        final_path = Path(tmp_dir) / "audio.mp3"

        cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 64k",
            "--output", str(output_path),
            "--no-playlist",
            "--remote-components", "ejs:github",  # 从 GitHub 下载 Deno challenge solver
        ]

        if cookies:
            cookies_file = Path(tmp_dir) / "cookies.txt"
            cookies_file.write_text(cookies, encoding="utf-8")
            cmd.extend(["--cookies", str(cookies_file)])

        cmd.append(youtube_url)

        logger.info(f"开始下载音频: {youtube_url}")
        loop = asyncio.get_event_loop()

        def _run_ytdlp():
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"yt-dlp 失败 (exit {result.returncode}):\n"
                    f"STDOUT: {result.stdout[-1000:]}\n"
                    f"STDERR: {result.stderr[-1000:]}"
                )
            return result

        await loop.run_in_executor(None, _run_ytdlp)

        if not final_path.exists():
            # yt-dlp 可能输出不同扩展名，找到实际文件
            candidates = list(Path(tmp_dir).glob("audio.*"))
            if not candidates:
                raise FileNotFoundError("yt-dlp 未生成音频文件")
            actual = candidates[0]
            if actual.suffix != ".mp3":
                # 用 ffmpeg 转换
                subprocess.run(
                    ["ffmpeg", "-i", str(actual), "-ac", "1", "-ar", "16000", "-b:a", "64k", str(final_path)],
                    check=True, capture_output=True
                )
                actual.unlink()
            else:
                actual.rename(final_path)

        size_mb = final_path.stat().st_size / 1024 / 1024
        logger.info(f"音频下载完成: {final_path.name}, {size_mb:.1f} MB")
        return final_path

    async def upload_to_storage(self, local_path: Path, job_id: str) -> str:
        """
        上传音频到 Supabase Storage public bucket
        返回公开访问 URL
        """
        storage_path = f"{job_id}.mp3"
        with open(local_path, "rb") as f:
            audio_bytes = f.read()

        logger.info(f"上传到 Supabase Storage: {storage_path}")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.supabase.storage.from_(AUDIO_BUCKET).upload(
                storage_path,
                audio_bytes,
                {"content-type": "audio/mpeg", "upsert": "true"}
            )
        )

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{AUDIO_BUCKET}/{storage_path}"
        logger.info(f"上传完成: {public_url}")
        return public_url

    async def delete_from_storage(self, job_id: str) -> None:
        """删除 Storage 中的音频文件（可选，节省空间）"""
        try:
            self.supabase.storage.from_(AUDIO_BUCKET).remove([f"{job_id}.mp3"])
        except Exception as e:
            logger.warning(f"删除 Storage 文件失败: {e}")
