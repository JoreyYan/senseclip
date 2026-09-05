"""
SRT字幕解析器
"""

import srt
from datetime import timedelta
from typing import List
from pathlib import Path
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.utterance import Utterance


class SRTParser:
    """SRT字幕解析器"""

    def __init__(self):
        self.parsed_count = 0

    def parse(self, file_path: str) -> List[Utterance]:
        """
        解析SRT文件

        Args:
            file_path: SRT文件路径

        Returns:
            Utterance列表

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式错误
        """
        # 检查文件是否存在
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 兼容：有些 SRT 文件可能被前置了标题/注释（比如以 # 开头的 markdown）。
        # 这里尝试定位第一条真正的 SRT block（序号行 + 时间戳行），从那里开始截断解析。
        content = content.lstrip("\ufeff")
        import re
        m = re.search(
            r"(?m)^\s*\d+\s*\n\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}",
            content,
        )
        if m:
            content = content[m.start():]

        # 解析SRT
        try:
            subtitles = list(srt.parse(content))
        except Exception as e:
            raise ValueError(f"SRT格式错误: {e}")

        # 转换为Utterance
        utterances = []
        for sub in subtitles:
            utterance = Utterance(
                id=sub.index,
                start_ms=self._to_milliseconds(sub.start),
                end_ms=self._to_milliseconds(sub.end),
                text=sub.content.strip(),
                duration_ms=self._to_milliseconds(sub.end - sub.start)
            )
            utterances.append(utterance)

        self.parsed_count = len(utterances)
        return utterances

    def _to_milliseconds(self, td: timedelta) -> int:
        """将timedelta转为毫秒"""
        return int(td.total_seconds() * 1000)
