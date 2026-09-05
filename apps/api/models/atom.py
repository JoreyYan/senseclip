"""
Atom模型 - 信息原子/微片段
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class Atom(BaseModel):
    """信息原子/微片段"""

    atom_id: str = Field(..., description="原子ID，如 A001")
    start_ms: int = Field(..., description="开始时间（毫秒）")
    end_ms: int = Field(..., description="结束时间（毫秒）")
    duration_ms: int = Field(..., description="持续时间（毫秒）")
    merged_text: str = Field(..., description="合并后的文本")
    type: str = Field(..., description="类型：fragment/complete_segment")
    completeness: str = Field(..., description="完整性：完整/需要上下文")
    source_utterance_ids: List[int] = Field(default_factory=list, description="来源字幕ID列表")

    # 可选字段（后续阶段填充）
    topics: Optional[List[str]] = Field(None, description="主题标注列表")
    emotion: Optional[Dict[str, Any]] = Field(None, description="情感标注: { type, score, confidence }")
    value: Optional[Dict[str, Any]] = Field(None, description="价值标注")
    embedding: Optional[List[float]] = Field(None, description="语义向量")

    # 实体标注
    entities: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="包含的实体: [{ name, type, confidence }]"
    )

    # 重要性评分
    importance_score: Optional[float] = Field(None, description="重要性评分 0-1")
    quality_score: Optional[float] = Field(None, description="内容质量评分 0-1")

    # 状态标记
    has_entity: Optional[bool] = Field(None, description="是否包含实体")
    has_topic: Optional[bool] = Field(None, description="是否包含主题")
    embedding_status: Optional[str] = Field(None, description="向量化状态: pending/completed/failed")

    # 关联信息
    parent_segment_id: Optional[str] = Field(None, description="所属时间段落ID (SEG_001, SEG_002...)")
    parent_narrative_id: Optional[str] = Field(None, description="所属叙事段落ID (NARRATIVE_001...)")

    # 上下文信息
    context_entities: Optional[List[str]] = Field(None, description="上下文相关实体")

    @property
    def start_time(self) -> str:
        """格式化开始时间"""
        return self._ms_to_time(self.start_ms)

    @property
    def end_time(self) -> str:
        """格式化结束时间"""
        return self._ms_to_time(self.end_ms)

    @property
    def duration_seconds(self) -> float:
        """持续时间（秒）"""
        return self.duration_ms / 1000.0

    def _ms_to_time(self, ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return self.model_dump()

    class Config:
        json_schema_extra = {
            "example": {
                "atom_id": "A001",
                "start_ms": 500000,
                "end_ms": 510000,
                "duration_ms": 10000,
                "merged_text": "1962年国民党残军撤到金三角，这是整个金三角问题的起源",
                "type": "fragment",
                "completeness": "完整",
                "source_utterance_ids": [85, 86, 87]
            }
        }
