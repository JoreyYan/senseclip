"""
NarrativeSegment模型 - 叙事片段（Level 2核心层）
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union


class NarrativeStructure(BaseModel):
    """叙事结构"""
    type: str = Field(..., description="叙事类型，如：历史叙事、观点论述、案例分析等")
    structure: str = Field(..., description="叙事结构描述，如：背景→危机→决策→结果")
    acts: List[Dict[str, str]] = Field(default_factory=list, description="叙事幕次，每一幕包含role和description")


class Topics(BaseModel):
    """主题标注"""
    primary_topic: Optional[str] = Field(None, description="主要话题")
    secondary_topics: List[str] = Field(default_factory=list, description="次要话题列表")
    free_tags: List[str] = Field(default_factory=list, description="自由标签（AI自动提取）")


class Entities(BaseModel):
    """实体提取"""
    persons: List[str] = Field(default_factory=list, description="人物")
    countries: List[str] = Field(default_factory=list, description="国家/地区")
    organizations: List[str] = Field(default_factory=list, description="组织/机构")
    time_points: List[str] = Field(default_factory=list, description="时间点")
    events: List[str] = Field(default_factory=list, description="历史事件")
    concepts: List[str] = Field(default_factory=list, description="概念/术语")


class ContentFacet(BaseModel):
    """内容维度"""
    type: str = Field(..., description="内容类型，如：历史叙述、观点论证、案例分析、数据展示")
    aspect: str = Field(..., description="关注点，如：历史事件全景、政策细节分析、人物决策动机")
    stance: str = Field(..., description="立场，如：中立客观、批判性分析、支持性论述")


class AIAnalysis(BaseModel):
    """AI深度分析"""
    core_argument: str = Field(..., description="核心论点/观点")
    key_insights: List[str] = Field(default_factory=list, description="关键洞察（3-5条）")
    logical_flow: str = Field(..., description="逻辑流程概括")
    suitable_for_reuse: bool = Field(True, description="是否适合二创复用")
    reuse_suggestions: List[str] = Field(default_factory=list, description="二创建议")


class NarrativeSegment(BaseModel):
    """叙事片段（Level 2核心层）- 3-15分钟完整叙事单元"""

    segment_id: str = Field(..., description="片段ID，如 SEG_001")
    title: str = Field(..., description="片段标题（AI生成）")
    atoms: List[Union[str, int]] = Field(..., description="包含的原子ID列表（支持字符串ID或整数索引）")

    # 时间信息
    start_ms: int = Field(..., description="开始时间（毫秒）")
    end_ms: int = Field(..., description="结束时间（毫秒）")
    duration_ms: int = Field(..., description="持续时间（毫秒）")

    # 内容
    summary: str = Field(..., description="内容摘要（150-300字）")
    full_text: str = Field(..., description="完整文本（所有原子文本合并）")

    # 核心标注（深度标注）
    narrative_structure: NarrativeStructure = Field(..., description="叙事结构")
    topics: Topics = Field(..., description="主题标注")
    entities: Entities = Field(..., description="实体提取")
    content_facet: ContentFacet = Field(..., description="内容维度")
    ai_analysis: AIAnalysis = Field(..., description="AI深度分析")

    # 语义向量（用于语义搜索）
    embedding: Optional[List[float]] = Field(None, description="语义向量（3072维）")

    # 元数据
    importance_score: float = Field(0.0, description="重要性评分（0-1）")
    quality_score: float = Field(0.0, description="质量评分（0-1）")

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

    @property
    def duration_minutes(self) -> float:
        """持续时间（分钟）"""
        return self.duration_ms / 60000.0

    @property
    def atom_count(self) -> int:
        """包含的原子数量"""
        return len(self.atoms)

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
                "segment_id": "SEG_003",
                "title": "布雷顿森林体系的崩溃（1971）",
                "atoms": ["A045", "A046", "A047", "A048", "A049", "A050"],
                "start_ms": 1350000,
                "end_ms": 1710000,
                "duration_ms": 360000,
                "summary": "讲述1971年美国单方面废除金本位，布雷顿森林体系崩溃的历史事件...",
                "full_text": "1944年布雷顿森林会议确立了...",
                "narrative_structure": {
                    "type": "历史叙事",
                    "structure": "背景铺垫 → 危机爆发 → 决策过程 → 结果影响",
                    "acts": [
                        {"role": "背景", "description": "布雷顿森林体系的建立背景"},
                        {"role": "危机", "description": "美元信任危机与黄金储备压力"},
                        {"role": "决策", "description": "尼克松关闭黄金窗口的决策"},
                        {"role": "结果", "description": "体系崩溃与美元霸权转型"}
                    ]
                },
                "topics": {
                    "primary_topic": "国际货币体系",
                    "secondary_topics": ["美元霸权", "金本位制"],
                    "free_tags": ["布雷顿森林体系", "1971年尼克松冲击", "黄金储备", "国际金融秩序"]
                },
                "entities": {
                    "persons": ["尼克松", "戴高乐"],
                    "countries": ["美国", "法国", "英国", "德国"],
                    "organizations": ["美联储", "IMF", "世界银行"],
                    "time_points": ["1944年", "1971年8月15日"],
                    "events": ["布雷顿森林会议", "布雷顿森林体系崩溃"],
                    "concepts": ["金本位", "美元霸权", "黄金窗口", "固定汇率"]
                },
                "content_facet": {
                    "type": "历史叙述",
                    "aspect": "历史事件全景",
                    "stance": "批判性分析"
                },
                "ai_analysis": {
                    "core_argument": "布雷顿森林体系崩溃标志着美元从金本位向信用本位的转型，奠定了现代美元霸权基础",
                    "key_insights": [
                        "黄金储备不足是体系崩溃的直接原因",
                        "越南战争造成巨额财政赤字加速了危机",
                        "尼克松的单方面决策体现了美国的霸权行为",
                        "体系崩溃后美元反而获得了更大的灵活性"
                    ],
                    "logical_flow": "历史背景 → 体系建立 → 矛盾累积 → 危机爆发 → 决策应对 → 长期影响",
                    "suitable_for_reuse": True,
                    "reuse_suggestions": [
                        "适合作为金融历史专题的核心片段",
                        "可与其他货币危机案例进行对比分析",
                        "适合制作\"美元霸权演变史\"系列内容"
                    ]
                },
                "importance_score": 0.95,
                "quality_score": 0.92
            }
        }


class SegmentMeta(BaseModel):
    """片段元数据（用于识别阶段）"""
    segment_num: int = Field(..., description="片段序号")
    atoms: List[Union[str, int]] = Field(..., description="包含的原子ID列表（支持字符串ID或整数索引）")
    start_ms: int = Field(..., description="开始时间（毫秒）")
    end_ms: int = Field(..., description="结束时间（毫秒）")
    duration_ms: int = Field(..., description="持续时间（毫秒）")
    reason: str = Field("", description="识别原因/理由")
    confidence: float = Field(1.0, description="置信度（0-1）")
    title: Optional[str] = Field(None, description="AI 生成的片段标题")
    summary: Optional[str] = Field(None, description="AI 生成的片段摘要")
    key_entities: Optional[List[str]] = Field(None, description="核心人物列表")
    topics: Optional[List[str]] = Field(None, description="关键词列表")

    @property
    def start_time(self) -> str:
        return self._ms_to_time(self.start_ms)

    @property
    def end_time(self) -> str:
        return self._ms_to_time(self.end_ms)

    @property
    def duration_minutes(self) -> float:
        return self.duration_ms / 60000.0

    def _ms_to_time(self, ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
