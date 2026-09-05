"""
数据转换器
将处理流水线的结果转换为 Supabase 数据格式
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from .models import *

logger = logging.getLogger(__name__)


class DataConverter:
    """数据转换器：处理流水线结果 -> Supabase 格式"""

    def __init__(self):
        self.logger = logger

    def _field(self, obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _topics_list(self, segment: Any) -> List[str]:
        topics = self._field(segment, 'topics', [])
        if isinstance(topics, dict):
            return [t for t in topics.values() if isinstance(t, str)]
        primary = self._field(topics, 'primary_topic')
        secondary = self._field(topics, 'secondary_topics', []) or []
        free_tags = self._field(topics, 'free_tags', []) or []
        values = [primary] if primary else []
        values.extend([t for t in secondary if t])
        values.extend([t for t in free_tags if t])
        return values

    def _key_entities_list(self, segment: Any) -> List[str]:
        entities = self._field(segment, 'key_entities')
        if isinstance(entities, list):
            return entities
        entities = entities or self._field(segment, 'entities')
        if not entities:
            return []
        names: List[str] = []
        for attr in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']:
            values = self._field(entities, attr, []) or []
            names.extend([v for v in values if v])
        return names

    def convert_processing_results_to_supabase(self,
                                              video_id: str,
                                              title: str,
                                              processing_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        将完整的处理结果转换为 Supabase 数据格式

        Args:
            video_id: 视频ID
            title: 视频标题
            processing_results: 包含所有处理结果的字典
                {
                    'atoms': List[Dict],                    # atoms.jsonl 数据
                    'atom_annotations': List[Dict],         # atom_annotations.json 数据
                    'narrative_segments': List[Dict],       # narrative_segments.json 数据
                    'entities': Dict,                       # entities.json 数据
                    'topics': Dict,                         # topics.json 数据
                    'duration_ms': int,                     # 视频总时长
                    'srt_file_path': str,                   # 字幕文件路径
                    'embeddings': Dict[str, List[float]]    # 原子向量 {atom_id: embedding}
                }

        Returns:
            Dict: 格式化的 Supabase 数据
        """
        self.logger.info(f"开始转换处理结果: {video_id}")

        # 基础数据提取
        atoms_data = processing_results.get('atoms', [])
        annotations_data = processing_results.get('atom_annotations', [])
        segments_data = processing_results.get('narrative_segments', [])
        entities_data = processing_results.get('entities', {})
        topics_data = processing_results.get('topics', {})
        duration_ms = processing_results.get('duration_ms', 0)
        srt_file_path = processing_results.get('srt_file_path', '')
        embeddings_data = processing_results.get('embeddings', {})

        # 转换结果
        supabase_data = {
            'video': self._convert_video_data(video_id, title, duration_ms, srt_file_path, len(atoms_data)),
            'segments': self._convert_segments_data(segments_data, video_id),
            'atoms': self._convert_atoms_data(atoms_data, video_id, segments_data),
            'atom_annotations': self._convert_annotations_data(annotations_data),
            'entities': self._convert_entities_data(entities_data, video_id),
            'atom_entities': self._convert_atom_entities_data(annotations_data),
            'topics': self._convert_topics_data(topics_data, video_id),
            'atom_topics': self._convert_atom_topics_data(annotations_data, topics_data),
            'embeddings': self._convert_embeddings_data(embeddings_data)
        }

        self.logger.info(f"数据转换完成: {video_id}, 包含 {len(atoms_data)} 个原子")
        return supabase_data

    def _convert_video_data(self, video_id: str, title: str, duration_ms: int,
                           srt_file_path: str, atom_count: int) -> Dict[str, Any]:
        """转换视频数据"""
        return {
            'id': video_id,
            'title': title,
            'duration_ms': duration_ms,
            'duration_seconds': duration_ms // 1000,
            'srt_file_path': srt_file_path,
            'status': 'processing',
            'processing_stage': 'data_processing',
            'atom_count': atom_count,
            'segment_count': 0,  # 后续更新
            'entity_count': 0    # 后续更新
        }

    def _convert_segments_data(self, segments_data: List[Dict], video_id: str) -> List[Dict[str, Any]]:
        """转换叙事片段数据"""
        if not segments_data:
            return []

        converted_segments = []
        for segment in segments_data:
            segment_id = self._field(segment, 'segment_id')
            if not segment_id:
                segment_num = self._field(segment, 'segment_num')
                if segment_num is not None:
                    segment_id = f"{video_id}_SEG_{int(segment_num):03d}"
            converted_segment = {
                'id': segment_id,
                'video_id': video_id,
                'start_ms': self._field(segment, 'start_ms'),
                'end_ms': self._field(segment, 'end_ms'),
                'duration_ms': self._field(segment, 'duration_ms'),
                'title': self._field(segment, 'title', f"Segment {segment_id or len(converted_segments) + 1}"),
                'summary': self._field(segment, 'summary', self._field(segment, 'reason', '')),
                'atom_count': self._field(segment, 'atom_count', len(self._field(segment, 'atoms', []) or [])),
                'topics': self._topics_list(segment),
                'key_entities': self._key_entities_list(segment),
                'narrative_type': self._field(segment, 'narrative_type')
            }
            converted_segments.append(converted_segment)

        return converted_segments

    def _convert_atoms_data(self, atoms_data: List[Dict], video_id: str,
                           segments_data: List[Dict]) -> List[Dict[str, Any]]:
        """转换原子数据"""
        if not atoms_data:
            return []

        # 建立原子ID到片段ID的映射
        atom_to_segment = self._build_atom_segment_mapping(atoms_data, segments_data, video_id)

        converted_atoms = []
        for atom in atoms_data:
            atom_id = atom.get('atom_id')
            converted_atom = {
                'id': atom_id,
                'video_id': video_id,
                'segment_id': atom_to_segment.get(atom_id),
                'start_ms': atom.get('start_ms'),
                'end_ms': atom.get('end_ms'),
                'duration_ms': atom.get('duration_ms'),
                'merged_text': atom.get('merged_text'),
                'start_time': atom.get('start_time'),
                'end_time': atom.get('end_time'),
                'duration_seconds': atom.get('duration_seconds'),
                'type': atom.get('type'),
                'completeness': atom.get('completeness'),
                'source_utterance_ids': atom.get('source_utterance_ids', [])
            }
            converted_atoms.append(converted_atom)

        return converted_atoms

    def _convert_annotations_data(self, annotations_data: List[Dict]) -> List[Dict[str, Any]]:
        """转换原子标注数据"""
        if not annotations_data:
            return []

        converted_annotations = []
        for annotation in annotations_data:
            emotion = annotation.get('emotion', {})
            converted_annotation = {
                'atom_id': annotation.get('atom_id'),
                'topics': annotation.get('topics', []),
                'emotion_type': emotion.get('type') if emotion else None,
                'emotion_score': emotion.get('score') if emotion else None,
                'emotion_confidence': emotion.get('confidence') if emotion else None,
                'emotion_distribution': emotion.get('distribution') if emotion else None,
                'importance_score': annotation.get('importance_score'),
                'has_entity': annotation.get('has_entity', False),
                'has_topic': annotation.get('has_topic', False),
                'embedding_status': annotation.get('embedding_status', 'pending'),
                'parent_segment_id': annotation.get('parent_segment_id'),
                'parent_narrative_id': annotation.get('parent_narrative_id')
            }
            converted_annotations.append(converted_annotation)

        return converted_annotations

    def _convert_entities_data(self, entities_data: Dict, video_id: str) -> List[Dict[str, Any]]:
        """转换全局实体数据"""
        if not entities_data or 'entities' not in entities_data:
            return []

        entities_list = entities_data['entities']
        converted_entities = []
        entity_type_map = {
            'person': 'PERSON',
            'persons': 'PERSON',
            'country': 'LOCATION',
            'countries': 'LOCATION',
            'location': 'LOCATION',
            'locations': 'LOCATION',
            'organization': 'ORGANIZATION',
            'organizations': 'ORGANIZATION',
            'event': 'EVENT',
            'events': 'EVENT',
            'time_point': 'EVENT',
            'time_points': 'EVENT',
            'concept': 'CONCEPT',
            'concepts': 'CONCEPT',
        }

        for entity in entities_list:
            raw_type = str(entity.get('entity_type') or '').strip()
            converted_entity = {
                'id': entity.get('entity_id'),
                'video_id': video_id,
                'name': entity.get('name'),
                'entity_type': entity_type_map.get(raw_type.lower(), raw_type.upper() if raw_type else 'CONCEPT'),
                'description': entity.get('description'),
                'aliases': entity.get('aliases', []),
                'mention_count': entity.get('mention_count', 0),
                'importance_score': entity.get('importance_score'),
                'first_mention_ms': entity.get('first_mention_ms'),
                'last_mention_ms': entity.get('last_mention_ms'),
                'mentioned_in_atoms': entity.get('mentioned_in_atoms', [])
            }
            converted_entities.append(converted_entity)

        return converted_entities

    def _convert_atom_entities_data(self, annotations_data: List[Dict]) -> List[Dict[str, Any]]:
        """转换原子-实体关联数据（从 annotations 的 entities 字段提取）"""
        if not annotations_data:
            return []

        atom_entities = []
        for annotation in annotations_data:
            atom_id = annotation.get('atom_id')
            entities = annotation.get('entities', [])

            for entity in entities:
                atom_entity = {
                    'atom_id': atom_id,
                    'entity_name': entity.get('name'),
                    'entity_type': entity.get('type'),
                    'confidence': entity.get('confidence', 0.0)
                }
                atom_entities.append(atom_entity)

        return atom_entities

    def _convert_topics_data(self, topics_data: Dict, video_id: str) -> List[Dict[str, Any]]:
        """转换话题数据"""
        if not topics_data or 'primary_topics' not in topics_data:
            return []

        primary_topics = topics_data['primary_topics']
        converted_topics = []

        def _normalize_atom_refs(atom_refs):
            normalized = []
            for atom_ref in atom_refs or []:
                if isinstance(atom_ref, int):
                    normalized.append(atom_ref)
                    continue
                text = str(atom_ref or '').strip()
                if not text:
                    continue
                digits = ''.join(ch for ch in text if ch.isdigit())
                if digits:
                    normalized.append(int(digits))
            return normalized

        for topic in primary_topics:
            converted_topic = {
                'video_id': video_id,
                'topic': topic.get('topic'),
                'weight': topic.get('weight'),
                'segments': topic.get('segments', []),
                'atoms': _normalize_atom_refs(topic.get('atoms', [])),
                'topic_type': 'primary'
            }
            converted_topics.append(converted_topic)

        return converted_topics

    def _convert_atom_topics_data(self, annotations_data: List[Dict],
                                 topics_data: Dict) -> List[Dict[str, Any]]:
        """转换原子-话题关联数据"""
        atom_topics = []

        # 从 annotations 中提取原子的话题
        for annotation in annotations_data:
            atom_id = annotation.get('atom_id')
            topics = annotation.get('topics', [])

            for topic in topics:
                atom_topic = {
                    'atom_id': atom_id,
                    'topic': topic,
                    'relevance_score': 1.0  # 默认权重
                }
                atom_topics.append(atom_topic)

        return atom_topics

    def _convert_embeddings_data(self, embeddings_data: Dict[str, List[float]], video_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """转换向量数据"""
        if not embeddings_data:
            return []

        from config import EMBEDDING_MODEL
        converted_embeddings = []
        for atom_id, embedding in embeddings_data.items():
            converted_embedding = {
                'atom_id': atom_id,
                'embedding': embedding,
                'embedding_model': EMBEDDING_MODEL,
                'status': 'active'
            }
            if video_id:
                converted_embedding['video_id'] = video_id
            converted_embeddings.append(converted_embedding)

        return converted_embeddings

    def _build_atom_segment_mapping(self, atoms_data: List[Dict],
                                   segments_data: List[Dict],
                                   video_id: str = "") -> Dict[str, str]:
        """构建原子ID到片段ID的映射"""
        atom_to_segment = {}

        for segment in segments_data:
            segment_id = self._field(segment, 'segment_id')
            if not segment_id:
                segment_num = self._field(segment, 'segment_num')
                if segment_num is not None:
                    segment_id = f"{video_id}_SEG_{int(segment_num):03d}" if video_id else f"SEG_{int(segment_num):03d}"
            segment_start = self._field(segment, 'start_ms', 0)
            segment_end = self._field(segment, 'end_ms', 0)

            # 根据时间范围匹配原子
            for atom in atoms_data:
                atom_id = atom.get('atom_id')
                atom_start = atom.get('start_ms', 0)

                # 如果原子的开始时间在片段范围内，则关联
                if segment_start <= atom_start < segment_end:
                    atom_to_segment[atom_id] = segment_id

        return atom_to_segment

    # ============================================================================
    # 从文件系统加载数据的便利方法
    # ============================================================================

    def load_processing_results_from_files(self, data_dir: str) -> Dict[str, Any]:
        """
        从文件系统加载处理结果

        Args:
            data_dir: 数据目录路径

        Returns:
            Dict: 处理结果数据
        """
        data_path = Path(data_dir)
        results = {}

        # 加载各种数据文件
        files_to_load = [
            ('atoms.jsonl', 'atoms'),
            ('atom_annotations.json', 'atom_annotations'),
            ('narrative_segments.json', 'narrative_segments'),
            ('entities.json', 'entities'),
            ('topics.json', 'topics'),
            ('overview.json', 'overview')
        ]

        for filename, key in files_to_load:
            file_path = data_path / filename
            if file_path.exists():
                try:
                    if filename.endswith('.jsonl'):
                        # JSONL 文件处理
                        data = []
                        with open(file_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    data.append(json.loads(line))
                        results[key] = data
                    else:
                        # 普通 JSON 文件
                        with open(file_path, 'r', encoding='utf-8') as f:
                            results[key] = json.load(f)

                    self.logger.info(f"已加载: {filename}")
                except Exception as e:
                    self.logger.error(f"加载 {filename} 失败: {e}")
            else:
                self.logger.warning(f"文件不存在: {filename}")

        # 从 overview.json 提取基础信息
        if 'overview' in results:
            overview = results['overview']
            results['duration_ms'] = overview.get('video', {}).get('duration_ms', 0)
            results['duration_seconds'] = overview.get('video', {}).get('duration_seconds', 0)

        return results

    def convert_from_directory(self, video_id: str, title: str,
                              data_dir: str, srt_file_path: str = None) -> Dict[str, Any]:
        """
        从目录加载数据并转换为 Supabase 格式

        Args:
            video_id: 视频ID
            title: 视频标题
            data_dir: 数据目录
            srt_file_path: 字幕文件路径

        Returns:
            Dict: Supabase 格式的数据
        """
        # 加载处理结果
        processing_results = self.load_processing_results_from_files(data_dir)

        # 添加额外信息
        processing_results['srt_file_path'] = srt_file_path or ''

        # 转换格式
        return self.convert_processing_results_to_supabase(video_id, title, processing_results)


# ============================================================================
# 便利函数
# ============================================================================

def convert_existing_data_to_supabase(video_id: str, title: str, data_dir: str) -> Dict[str, Any]:
    """
    便利函数：转换现有数据目录到 Supabase 格式

    Args:
        video_id: 视频ID
        title: 视频标题
        data_dir: 现有数据目录路径

    Returns:
        Dict: Supabase 格式的数据
    """
    converter = DataConverter()
    return converter.convert_from_directory(video_id, title, data_dir)
