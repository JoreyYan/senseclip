"""
段落详情数据模型
定义三级分析层次的数据结构：原子级别、段落级别、叙事段落级别
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from .entity_index import AtomAnnotation


class AtomDetailView(BaseModel):
    """原子详细视图 - 用于前端展示"""
    atom_id: str
    text_snippet: str = Field(description="文本片段")
    start_ms: int
    end_ms: int
    duration_ms: int

    # 语义标注
    topics: List[str] = Field(default_factory=list, description="关联主题")
    entities: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="包含的实体: [{ name, type, confidence }]"
    )
    emotion: Optional[Dict[str, Any]] = Field(None, description="情感分析: { type, score, confidence }")

    # 重要性和质量
    importance_score: float = Field(default=0.5, description="重要性评分 0-1")
    quality_score: float = Field(default=0.5, description="内容质量评分 0-1")

    # 状态标记
    has_entity: bool = Field(default=False, description="是否包含实体")
    has_topic: bool = Field(default=False, description="是否包含主题")
    embedding_status: str = Field(default="pending", description="向量化状态")

    # 关联信息
    parent_segment_id: Optional[str] = Field(None, description="所属时间段落ID")
    parent_narrative_id: Optional[str] = Field(None, description="所属叙事段落ID")


class SegmentLevelAnalysis(BaseModel):
    """段落级别分析结果"""
    segment_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    start_time_str: str
    end_time_str: str

    # 原子统计
    total_atoms: int = Field(description="总原子数量")
    analyzed_atoms: int = Field(description="已分析原子数量")

    # 提取结果统计
    total_entities: int = Field(default=0, description="实体总数")
    total_topics: int = Field(default=0, description="主题总数")
    avg_importance: float = Field(default=0.5, description="平均重要性")

    # 实体分布
    entity_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="实体类型分布: { 'person': 5, 'organization': 3 }"
    )

    # 主题分布
    topic_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="主题分布统计"
    )

    # 情感分析
    emotion_summary: Optional[Dict[str, Any]] = Field(
        None,
        description="段落整体情感: { dominant_emotion, confidence, distribution }"
    )

    # 质量评估
    content_quality: Dict[str, Any] = Field(
        default_factory=dict,
        description="内容质量评估: { clarity, relevance, completeness }"
    )


class NarrativeSegmentAnalysis(BaseModel):
    """叙事段落级别分析"""
    narrative_id: str
    title: str = Field(description="叙事段落标题")
    summary: str = Field(description="段落摘要")

    # 时间范围（可能跨越多个时间段落）
    start_ms: int
    end_ms: int
    duration_ms: int

    # 包含的时间段落
    time_segments: List[str] = Field(description="包含的时间段落ID列表")

    # 叙事结构
    narrative_structure: Dict[str, Any] = Field(
        default_factory=dict,
        description="叙事结构: { theme, conflict, resolution, characters }"
    )

    # 关键实体
    key_entities: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="关键实体及其作用"
    )

    # 主要话题
    main_topics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="主要话题和讨论点"
    )

    # 情感弧线
    emotion_arc: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="情感变化轨迹"
    )

    # 重要性评分
    narrative_importance: float = Field(default=0.5, description="叙事重要性")


class SegmentDetailAnalysis(BaseModel):
    """完整的段落详情分析 - 三个层级的汇总"""
    segment_id: str
    analysis_timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="分析时间戳"
    )

    # 原子级别详情
    atom_level: List[AtomDetailView] = Field(
        description="原子级别分析结果列表"
    )

    # 段落级别分析
    segment_level: SegmentLevelAnalysis = Field(
        description="段落级别分析汇总"
    )

    # 叙事段落级别（如果属于某个叙事段落）
    narrative_level: Optional[NarrativeSegmentAnalysis] = Field(
        None,
        description="叙事段落级别分析（如适用）"
    )

    # 分析状态
    analysis_status: Dict[str, str] = Field(
        default_factory=lambda: {
            "atom_analysis": "pending",
            "segment_analysis": "pending",
            "narrative_analysis": "pending"
        },
        description="各层级分析状态"
    )

    # 分析统计
    analysis_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="分析统计信息"
    )


class SegmentDetailService(BaseModel):
    """段落详情服务 - 用于构建和管理详情数据"""

    def build_atom_detail_view(
        self,
        atom: Any,
        annotation: AtomAnnotation
    ) -> AtomDetailView:
        """构建原子详细视图"""
        return AtomDetailView(
            atom_id=annotation.atom_id,
            text_snippet=getattr(atom, 'merged_text', '')[:200] + "..." if len(getattr(atom, 'merged_text', '')) > 200 else getattr(atom, 'merged_text', ''),
            start_ms=getattr(atom, 'start_ms', 0),
            end_ms=getattr(atom, 'end_ms', 0),
            duration_ms=getattr(atom, 'end_ms', 0) - getattr(atom, 'start_ms', 0),
            topics=annotation.topics,
            entities=annotation.entities,
            emotion=annotation.emotion,
            importance_score=annotation.importance_score,
            has_entity=annotation.has_entity,
            has_topic=annotation.has_topic,
            embedding_status=annotation.embedding_status,
            parent_segment_id=annotation.parent_segment_id,
            parent_narrative_id=annotation.parent_narrative_id
        )

    def build_segment_level_analysis(
        self,
        segment: Any,
        atom_details: List[AtomDetailView]
    ) -> SegmentLevelAnalysis:
        """构建段落级别分析"""
        # 统计实体和主题
        all_entities = []
        all_topics = []
        importance_scores = []

        for atom_detail in atom_details:
            all_entities.extend(atom_detail.entities)
            all_topics.extend(atom_detail.topics)
            importance_scores.append(atom_detail.importance_score)

        # 计算实体类型分布
        entity_distribution = {}
        for entity in all_entities:
            entity_type = entity.get('type', 'unknown')
            entity_distribution[entity_type] = entity_distribution.get(entity_type, 0) + 1

        # 计算主题分布
        topic_distribution = {}
        for topic in all_topics:
            topic_distribution[topic] = topic_distribution.get(topic, 0) + 1

        return SegmentLevelAnalysis(
            segment_id=segment.segment_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            duration_ms=segment.duration_ms,
            start_time_str=segment.start_time_str,
            end_time_str=segment.end_time_str,
            total_atoms=len(atom_details),
            analyzed_atoms=len([a for a in atom_details if a.has_entity or a.has_topic]),
            total_entities=len(set(e['name'] for e in all_entities)),
            total_topics=len(set(all_topics)),
            avg_importance=sum(importance_scores) / len(importance_scores) if importance_scores else 0.5,
            entity_distribution=entity_distribution,
            topic_distribution=topic_distribution
        )

    def build_complete_segment_detail(
        self,
        segment: Any,
        atoms: List[Any],
        annotations: List[AtomAnnotation],
        narrative_segment: Optional[Any] = None
    ) -> SegmentDetailAnalysis:
        """构建完整的段落详情分析"""
        # 构建原子详细视图
        atom_details = []
        annotations_dict = {ann.atom_id: ann for ann in annotations}

        for atom in atoms:
            atom_id = getattr(atom, 'atom_id', atom.get('atom_id') if isinstance(atom, dict) else '')
            if atom_id in annotations_dict:
                atom_detail = self.build_atom_detail_view(atom, annotations_dict[atom_id])
                atom_details.append(atom_detail)

        # 构建段落级别分析
        segment_analysis = self.build_segment_level_analysis(segment, atom_details)

        # 构建叙事段落分析（如果存在）
        narrative_analysis = None
        if narrative_segment:
            narrative_analysis = NarrativeSegmentAnalysis(
                narrative_id=getattr(narrative_segment, 'id', 'unknown'),
                title=getattr(narrative_segment, 'title', ''),
                summary=getattr(narrative_segment, 'summary', ''),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                duration_ms=segment.duration_ms,
                time_segments=[segment.segment_id],
                narrative_importance=getattr(narrative_segment, 'importance', 0.5)
            )

        return SegmentDetailAnalysis(
            segment_id=segment.segment_id,
            atom_level=atom_details,
            segment_level=segment_analysis,
            narrative_level=narrative_analysis,
            analysis_stats={
                "total_atoms_analyzed": len(atom_details),
                "entities_found": segment_analysis.total_entities,
                "topics_found": segment_analysis.total_topics,
                "avg_importance": segment_analysis.avg_importance
            }
        )