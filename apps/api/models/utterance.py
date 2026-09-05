"""
Utterance模型 - 单句字幕
"""

from pydantic import BaseModel, Field
from typing import Optional


class Utterance(BaseModel):
    """单句字幕"""

    id: int = Field(..., description="序号")
    start_ms: int = Field(..., description="开始时间（毫秒）")
    end_ms: int = Field(..., description="结束时间（毫秒）")
    text: str = Field(..., description="文本内容")
    duration_ms: int = Field(..., description="持续时间（毫秒）")

    @property
    def start_time(self) -> str:
        """格式化开始时间 HH:MM:SS"""
        return self._ms_to_time(self.start_ms)

    @property
    def end_time(self) -> str:
        """格式化结束时间 HH:MM:SS"""
        return self._ms_to_time(self.end_ms)

    def _ms_to_time(self, ms: int) -> str:
        """毫秒转时间字符串"""
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "start_ms": 8933,
                "end_ms": 10400,
                "text": "开始了没有啊",
                "duration_ms": 1467
            }
        }
