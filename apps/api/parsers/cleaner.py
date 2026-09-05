"""
字幕清洗器 - 简化版（只做格式标准化）
"""

from typing import List
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.utterance import Utterance


class Cleaner:
    """字幕清洗器 - 极简版"""

    def __init__(self):
        self.removed_count = 0

    def clean(self, utterances: List[Utterance]) -> List[Utterance]:
        """
        清洗字幕（只做格式标准化）

        规则：
        1. 标准化文本格式（去除换行、多余空格）
        2. 过滤完全空的字幕

        Args:
            utterances: 原始字幕列表

        Returns:
            清洗后的字幕列表
        """
        cleaned = []

        for utt in utterances:
            # 标准化文本格式
            cleaned_text = self._normalize_text(utt.text)

            # 只过滤完全空的字幕
            if not cleaned_text:
                self.removed_count += 1
                continue

            # 更新文本
            utt.text = cleaned_text
            cleaned.append(utt)

        return cleaned

    def _normalize_text(self, text: str) -> str:
        """标准化文本"""
        # 去除换行符
        text = text.replace('\n', ' ')
        # 去除多余空格
        text = ' '.join(text.split())
        # 去除首尾空格
        text = text.strip()
        return text
