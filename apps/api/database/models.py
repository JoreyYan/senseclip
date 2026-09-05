"""
数据库模型定义
对应 Supabase 表结构的 Python 类
"""

from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from dataclasses import dataclass
from enum import Enum


class VideoStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class EmotionType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EntityType(str, Enum):
    PERSON = "PERSON"
    LOCATION = "LOCATION"
    ORGANIZATION = "ORGANIZATION"
    EVENT = "EVENT"
    CONCEPT = "CONCEPT"


class EmbeddingStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Project:
    """项目模型"""
    id: str
    title: str
    description: Optional[str] = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status.value,
        }


@dataclass
class Video:
    """视频模型"""
    id: str
    title: str
    duration_ms: int
    duration_seconds: int
    video_url: Optional[str] = None
    status: VideoStatus = VideoStatus.PROCESSING
    processing_stage: Optional[str] = None
    atom_count: int = 0
    segment_count: int = 0
    entity_count: int = 0
    data_dir: Optional[str] = None
    srt_file_path: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'duration_ms': self.duration_ms,
            'duration_seconds': self.duration_seconds,
            'video_url': self.video_url,
            'status': self.status.value,
            'processing_stage': self.processing_stage,
            'atom_count': self.atom_count,
            'segment_count': self.segment_count,
            'entity_count': self.entity_count,
            'data_dir': self.data_dir,
            'srt_file_path': self.srt_file_path,
        }


@dataclass
class NarrativeSegment:
    """叙事片段模型"""
    id: str
    video_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    title: str
    summary: str
    atom_count: int = 0
    topics: List[str] = None
    key_entities: List[str] = None
    narrative_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.topics is None:
            self.topics = []
        if self.key_entities is None:
            self.key_entities = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'video_id': self.video_id,
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'duration_ms': self.duration_ms,
            'title': self.title,
            'summary': self.summary,
            'atom_count': self.atom_count,
            'topics': self.topics,
            'key_entities': self.key_entities,
            'narrative_type': self.narrative_type,
        }


@dataclass
class Atom:
    """原子模型"""
    id: str
    video_id: str
    segment_id: Optional[str]
    start_ms: int
    end_ms: int
    duration_ms: int
    merged_text: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    type: Optional[str] = None
    completeness: Optional[str] = None
    source_utterance_ids: List[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.source_utterance_ids is None:
            self.source_utterance_ids = []
        if self.duration_seconds is None:
            self.duration_seconds = self.duration_ms / 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'video_id': self.video_id,
            'segment_id': self.segment_id,
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'duration_ms': self.duration_ms,
            'merged_text': self.merged_text,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration_seconds': self.duration_seconds,
            'type': self.type,
            'completeness': self.completeness,
            'source_utterance_ids': self.source_utterance_ids,
        }


@dataclass
class AtomAnnotation:
    """原子标注模型"""
    atom_id: str
    topics: List[str] = None
    emotion_type: Optional[EmotionType] = None
    emotion_score: Optional[float] = None
    emotion_confidence: Optional[float] = None
    emotion_distribution: Optional[Dict[str, float]] = None
    importance_score: Optional[float] = None
    has_entity: bool = False
    has_topic: bool = False
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    parent_segment_id: Optional[str] = None
    parent_narrative_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.topics is None:
            self.topics = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'atom_id': self.atom_id,
            'topics': self.topics,
            'emotion_type': self.emotion_type.value if self.emotion_type else None,
            'emotion_score': self.emotion_score,
            'emotion_confidence': self.emotion_confidence,
            'emotion_distribution': self.emotion_distribution,
            'importance_score': self.importance_score,
            'has_entity': self.has_entity,
            'has_topic': self.has_topic,
            'embedding_status': self.embedding_status.value,
            'parent_segment_id': self.parent_segment_id,
            'parent_narrative_id': self.parent_narrative_id,
        }


@dataclass
class Entity:
    """实体模型"""
    id: str
    name: str
    entity_type: EntityType
    description: Optional[str] = None
    aliases: List[str] = None
    mention_count: int = 0
    importance_score: Optional[float] = None
    first_mention_ms: Optional[int] = None
    last_mention_ms: Optional[int] = None
    mentioned_in_atoms: List[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []
        if self.mentioned_in_atoms is None:
            self.mentioned_in_atoms = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'entity_type': self.entity_type.value,
            'description': self.description,
            'aliases': self.aliases,
            'mention_count': self.mention_count,
            'importance_score': self.importance_score,
            'first_mention_ms': self.first_mention_ms,
            'last_mention_ms': self.last_mention_ms,
            'mentioned_in_atoms': self.mentioned_in_atoms,
        }


@dataclass
class Topic:
    """话题模型"""
    topic: str
    weight: Optional[float] = None
    segments: List[str] = None
    atoms: List[int] = None
    topic_type: str = "primary"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.segments is None:
            self.segments = []
        if self.atoms is None:
            self.atoms = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'topic': self.topic,
            'weight': self.weight,
            'segments': self.segments,
            'atoms': self.atoms,
            'topic_type': self.topic_type,
        }


@dataclass
class AtomEntity:
    """原子-实体关联模型"""
    atom_id: str
    entity_name: str
    entity_type: str
    confidence: float
    global_entity_id: Optional[str] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'atom_id': self.atom_id,
            'entity_name': self.entity_name,
            'entity_type': self.entity_type,
            'confidence': self.confidence,
            'global_entity_id': self.global_entity_id,
        }


@dataclass
class AtomEmbedding:
    """原子向量模型"""
    atom_id: str
    embedding: List[float]
    embedding_model: str = "text-embedding-ada-002"
    embedding_version: Optional[str] = None
    status: str = "active"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'atom_id': self.atom_id,
            'embedding': self.embedding,
            'embedding_model': self.embedding_model,
            'embedding_version': self.embedding_version,
            'status': self.status,
        }


# ============================================================================
# 转换函数
# ============================================================================

def convert_legacy_atom_to_model(atom_data: Dict[str, Any], video_id: str, segment_id: str = None) -> Atom:
    """将现有的 atoms.jsonl 数据转换为 Atom 模型"""
    return Atom(
        id=atom_data.get('atom_id'),
        video_id=video_id,
        segment_id=segment_id,
        start_ms=atom_data.get('start_ms'),
        end_ms=atom_data.get('end_ms'),
        duration_ms=atom_data.get('duration_ms'),
        merged_text=atom_data.get('merged_text'),
        start_time=atom_data.get('start_time'),
        end_time=atom_data.get('end_time'),
        duration_seconds=atom_data.get('duration_seconds'),
        type=atom_data.get('type'),
        completeness=atom_data.get('completeness'),
        source_utterance_ids=atom_data.get('source_utterance_ids', [])
    )


def convert_legacy_annotation_to_model(annotation_data: Dict[str, Any]) -> AtomAnnotation:
    """将现有的 atom_annotations.json 数据转换为 AtomAnnotation 模型"""
    emotion_data = annotation_data.get('emotion', {})

    return AtomAnnotation(
        atom_id=annotation_data.get('atom_id'),
        topics=annotation_data.get('topics', []),
        emotion_type=EmotionType(emotion_data.get('type')) if emotion_data and emotion_data.get('type') else None,
        emotion_score=emotion_data.get('score') if emotion_data else None,
        emotion_confidence=emotion_data.get('confidence') if emotion_data else None,
        emotion_distribution=emotion_data.get('distribution') if emotion_data else None,
        importance_score=annotation_data.get('importance_score'),
        has_entity=annotation_data.get('has_entity', False),
        has_topic=annotation_data.get('has_topic', False),
        embedding_status=EmbeddingStatus(annotation_data.get('embedding_status', 'pending')),
        parent_segment_id=annotation_data.get('parent_segment_id'),
        parent_narrative_id=annotation_data.get('parent_narrative_id')
    )