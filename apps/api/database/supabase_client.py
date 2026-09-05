"""
Supabase 数据库客户端
统一管理所有数据库操作
"""

import os
import logging
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
import json

try:
    from supabase import create_client, Client
    import numpy as np
except ImportError as e:
    print(f"请安装必需的包: pip install supabase numpy")
    raise e

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Supabase 数据库客户端"""

    def __init__(self, url: str = None, key: str = None):
        """
        初始化 Supabase 客户端

        Args:
            url: Supabase URL，如果为空则从环境变量读取
            key: Supabase API Key，如果为空则从环境变量读取
        """
        self.url = url or os.getenv('SUPABASE_URL')
        self.key = key or os.getenv('SUPABASE_KEY')

        if not self.url or not self.key:
            raise ValueError("请设置 SUPABASE_URL 和 SUPABASE_KEY 环境变量")

        self.client: Client = create_client(self.url, self.key)
        logger.info("Supabase 客户端初始化完成")

    # ============================================================================
    # 项目管理
    # ============================================================================

    def create_project(self, title: str, description: str = None) -> Dict[str, Any]:
        """创建项目"""
        data = {
            'title': title,
            'description': description,
            'status': 'active'
        }

        result = self.client.table('projects').insert(data).execute()
        if result.data:
            logger.info(f"项目创建成功: {title}")
            return result.data[0]
        else:
            raise Exception(f"项目创建失败: {result}")

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """获取项目信息"""
        result = self.client.table('projects').select("*").eq('id', project_id).execute()
        return result.data[0] if result.data else None

    def list_projects(self) -> List[Dict[str, Any]]:
        """获取所有项目"""
        result = self.client.table('projects').select("*").order('created_at', desc=True).execute()
        return result.data

    # ============================================================================
    # 视频管理
    # ============================================================================

    def create_video(self, video_id: str, title: str, duration_ms: int,
                    srt_file_path: str = None, project_id: str = None) -> Dict[str, Any]:
        """创建视频记录"""
        data = {
            'id': video_id,
            'title': title,
            'duration_ms': duration_ms,
            'duration_seconds': duration_ms // 1000,
            'srt_file_path': srt_file_path,
            'status': 'processing',
            'processing_stage': 'subtitle_uploaded'
        }

        result = self.client.table('videos').insert(data).execute()
        if result.data:
            video_data = result.data[0]

            # 如果指定了项目，添加关联
            if project_id:
                self.add_video_to_project(project_id, video_id)

            logger.info(f"视频记录创建成功: {video_id}")
            return video_data
        else:
            raise Exception(f"视频记录创建失败: {result}")

    def upsert_video(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update a video record idempotently."""
        result = self.client.table('videos').upsert(data, on_conflict='id').execute()
        if result.data:
            logger.info(f"视频记录创建/更新成功: {data.get('id')}")
            return result.data[0]
        raise Exception(f"视频记录创建/更新失败: {result}")

    def update_video_status(self, video_id: str, status: str, processing_stage: str = None) -> bool:
        """更新视频处理状态"""
        data = {'status': status}
        if processing_stage:
            data['processing_stage'] = processing_stage

        result = self.client.table('videos').update(data).eq('id', video_id).execute()
        if result.data:
            logger.info(f"视频状态更新: {video_id} -> {status}")
            return True
        return False

    def get_video(self, video_id: str) -> Optional[Dict[str, Any]]:
        """获取视频信息"""
        result = self.client.table('videos').select("*").eq('id', video_id).execute()
        return result.data[0] if result.data else None

    def add_video_to_project(self, project_id: str, video_id: str) -> bool:
        """将视频添加到项目"""
        data = {
            'project_id': project_id,
            'video_id': video_id
        }

        try:
            result = self.client.table('project_videos').insert(data).execute()
            if result.data:
                logger.info(f"视频已添加到项目: {video_id} -> {project_id}")
                return True
        except Exception as e:
            if "duplicate key" in str(e).lower():
                logger.warning(f"视频已在项目中: {video_id}")
                return True
            raise e
        return False

    # ============================================================================
    # 叙事片段管理
    # ============================================================================

    def create_narrative_segments(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建或更新叙事片段（使用upsert避免重复执行冲突）"""
        if not segments:
            return []

        # 使用 upsert 替代 insert，自动处理重复的 segment_id
        result = self.client.table('narrative_segments').upsert(segments, on_conflict='id').execute()
        if result.data:
            logger.info(f"叙事片段创建/更新成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"叙事片段创建/更新失败: {result}")

    def create_narrative_segment_single(self, segment: Dict[str, Any]) -> Dict[str, Any]:
        """创建或更新单个叙事片段（增量上传）"""
        result = self.client.table('narrative_segments').upsert(segment, on_conflict='id').execute()
        if result.data:
            logger.info(f"叙事片段创建/更新成功: {segment.get('id', 'unknown')}")
            return result.data[0]
        else:
            raise Exception(f"叙事片段创建/更新失败: {result}")

    def create_narrative_segment_from_object(self, narrative_segment, video_id: str) -> Dict[str, Any]:
        """
        从 NarrativeSegment 对象创建片段（便捷方法）

        Args:
            narrative_segment: NarrativeSegment 对象
            video_id: 视频ID

        Returns:
            上传后的片段数据
        """
        # 转换为字典
        segment_dict = narrative_segment.to_dict()

        # 转换为 Supabase 格式
        segment_data = {
            'id': segment_dict.get('segment_id'),
            'video_id': video_id,
            'start_ms': segment_dict.get('start_ms'),
            'end_ms': segment_dict.get('end_ms'),
            'duration_ms': segment_dict.get('duration_ms'),
            'title': segment_dict.get('title'),
            'summary': segment_dict.get('summary'),
            'atom_count': len(segment_dict.get('atoms', [])),
            # 提取topics数组
            'topics': segment_dict.get('topics', {}).get('free_tags', []),
            # 提取关键实体
            'key_entities': (
                segment_dict.get('entities', {}).get('persons', [])[:5] +
                segment_dict.get('entities', {}).get('organizations', [])[:3]
            ),
            'narrative_type': segment_dict.get('narrative_structure', {}).get('type')
        }

        return self.create_narrative_segment_single(segment_data)

    def get_segments_by_video(self, video_id: str) -> List[Dict[str, Any]]:
        """获取视频的所有片段"""
        result = self.client.table('narrative_segments').select("*").eq('video_id', video_id).order('start_ms').execute()
        return result.data

    # ============================================================================
    # 原子管理
    # ============================================================================

    def create_atoms(self, atoms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建原子"""
        if not atoms:
            return []

        # 去重：同一批次里不能有重复 id（ON CONFLICT DO UPDATE 不允许）
        seen: dict = {}
        for atom in atoms:
            atom_id = atom.get('id')
            if atom_id:
                seen[atom_id] = atom
        atoms = list(seen.values())

        # 分批插入，避免一次性插入太多数据
        batch_size = 100
        all_results = []

        for i in range(0, len(atoms), batch_size):
            batch = atoms[i:i + batch_size]
            result = self.client.table('atoms').upsert(batch, on_conflict='id').execute()
            if result.data:
                all_results.extend(result.data)
            else:
                raise Exception(f"原子批次创建失败: {result}")

        logger.info(f"原子创建成功: {len(all_results)} 个")
        return all_results

    def get_atoms_by_video(self, video_id: str) -> List[Dict[str, Any]]:
        """获取视频的所有原子"""
        result = self.client.table('atoms').select("*").eq('video_id', video_id).order('start_ms').execute()
        return result.data

    def get_atom_details(self, atom_id: str = None, video_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取原子详情（使用视图）"""
        query = self.client.table('atom_details').select("*")

        if atom_id:
            query = query.eq('atom_id', atom_id)
        elif video_id:
            query = query.eq('video_id', video_id)

        query = query.limit(limit)
        result = query.execute()
        return result.data

    # ============================================================================
    # 原子标注管理
    # ============================================================================

    def create_atom_annotations(self, annotations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建原子标注"""
        if not annotations:
            return []

        result = self.client.table('atom_annotations').upsert(annotations, on_conflict='atom_id').execute()
        if result.data:
            logger.info(f"原子标注创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"原子标注创建失败: {result}")

    def get_atom_annotations_by_ids(self, atom_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Load atom annotations keyed by atom_id."""
        if not atom_ids:
            return {}
        result = self.client.table('atom_annotations').select("*").in_('atom_id', atom_ids).execute()
        return {item['atom_id']: item for item in (result.data or [])}

    # ============================================================================
    # 实体管理
    # ============================================================================

    def create_entities(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建实体"""
        if not entities:
            return []

        result = self.client.table('entities').insert(entities).execute()
        if result.data:
            logger.info(f"实体创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"实体创建失败: {result}")

    def _is_missing_column_error(self, error: Exception, table: str, column: str) -> bool:
        message = str(error).lower()
        return (
            f"column {table}.{column} does not exist" in message or
            f"'{table}.{column}'" in message or
            (f"column {column}" in message and "does not exist" in message)
        )

    def _strip_field(self, rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
        return [{k: v for k, v in row.items() if k != field} for row in rows]

    def _upsert_global_entities(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized = self._strip_field(entities, 'video_id')
        if not sanitized:
            return []
        result = self.client.table('entities').upsert(sanitized, on_conflict='id').execute()
        return result.data or []

    def get_entities_by_video(self, video_id: str) -> List[Dict[str, Any]]:
        """Load persisted entities for a video."""
        try:
            result = self.client.table('entities').select("*").eq('video_id', video_id).execute()
            return result.data or []
        except Exception as exc:
            if self._is_missing_column_error(exc, 'entities', 'video_id'):
                logger.warning("entities table has no video_id column; skip per-video entity load")
                return []
            raise

    def replace_entities(self, video_id: str, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace all entities for a video."""
        try:
            self.client.table('entities').delete().eq('video_id', video_id).execute()
            if not entities:
                return []
            return self.create_entities(entities)
        except Exception as exc:
            if self._is_missing_column_error(exc, 'entities', 'video_id'):
                logger.warning("entities table has no video_id column; falling back to global entity upsert")
                return self._upsert_global_entities(entities)
            raise

    def create_atom_entities(self, atom_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建原子-实体关联"""
        if not atom_entities:
            return []

        result = self.client.table('atom_entities').insert(atom_entities).execute()
        if result.data:
            logger.info(f"原子-实体关联创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"原子-实体关联创建失败: {result}")

    def get_atom_entities_by_ids(self, atom_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Load atom_entities rows grouped by atom_id."""
        if not atom_ids:
            return {}
        result = self.client.table('atom_entities').select("*").in_('atom_id', atom_ids).execute()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in (result.data or []):
            grouped.setdefault(item['atom_id'], []).append(item)
        return grouped

    def replace_atom_entities(self, atom_ids: List[str], atom_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace atom_entities rows for the given atoms."""
        if atom_ids:
            batch_size = 100
            for i in range(0, len(atom_ids), batch_size):
                self.client.table('atom_entities').delete().in_('atom_id', atom_ids[i:i + batch_size]).execute()
        if not atom_entities:
            return []
        return self.create_atom_entities(atom_entities)

    def save_atom_annotations(self, annotations: List[Dict[str, Any]], video_id: Optional[str] = None) -> bool:
        """Persist atom annotations and their extracted entities idempotently."""
        if not annotations:
            return True
        annotation_rows = []
        atom_entity_rows = []
        atom_ids = []
        for annotation in annotations:
            atom_id = annotation.get('atom_id')
            if not atom_id:
                continue
            atom_ids.append(atom_id)
            emotion = annotation.get('emotion', {}) or {}
            annotation_rows.append({
                'atom_id': atom_id,
                'topics': annotation.get('topics', []),
                'emotion_type': emotion.get('type'),
                'emotion_score': emotion.get('score'),
                'emotion_confidence': emotion.get('confidence'),
                'emotion_distribution': emotion.get('distribution'),
                'importance_score': annotation.get('importance_score'),
                'has_entity': annotation.get('has_entity', False),
                'has_topic': annotation.get('has_topic', False),
                'embedding_status': annotation.get('embedding_status', 'pending'),
                'parent_segment_id': annotation.get('parent_segment_id'),
                'parent_narrative_id': annotation.get('parent_narrative_id'),
            })
            for entity in annotation.get('entities', []) or []:
                row = {
                    'atom_id': atom_id,
                    'entity_name': entity.get('name'),
                    'entity_type': entity.get('type'),
                    'confidence': entity.get('confidence', 0.0),
                }
                if video_id:
                    row['video_id'] = video_id
                atom_entity_rows.append(row)
        self.create_atom_annotations(annotation_rows)
        self.replace_atom_entities(atom_ids, atom_entity_rows)
        return True

    # ============================================================================
    # 话题管理
    # ============================================================================

    def create_topics(self, topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建话题"""
        if not topics:
            return []

        result = self.client.table('topics').insert(topics).execute()
        if result.data:
            logger.info(f"话题创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"话题创建失败: {result}")

    def _upsert_global_topics(self, topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized = self._strip_field(topics, 'video_id')
        if not sanitized:
            return []
        result = self.client.table('topics').upsert(sanitized, on_conflict='topic').execute()
        return result.data or []

    def _get_topic_ids_by_names(self, topic_names: List[str]) -> Dict[str, int]:
        names = [name for name in dict.fromkeys(topic_names) if name]
        if not names:
            return {}
        result = self.client.table('topics').select("id,topic").in_('topic', names).execute()
        return {row['topic']: row['id'] for row in (result.data or []) if row.get('topic') is not None}

    def get_topics_by_video(self, video_id: str) -> List[Dict[str, Any]]:
        """Load persisted topics for a video."""
        try:
            result = self.client.table('topics').select("*").eq('video_id', video_id).execute()
            return result.data or []
        except Exception as exc:
            if self._is_missing_column_error(exc, 'topics', 'video_id'):
                logger.warning("topics table has no video_id column; skip per-video topic load")
                return []
            raise

    def replace_topics(self, video_id: str, topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace all topics for a video."""
        try:
            self.client.table('topics').delete().eq('video_id', video_id).execute()
            if not topics:
                return []
            return self.create_topics(topics)
        except Exception as exc:
            if self._is_missing_column_error(exc, 'topics', 'video_id'):
                logger.warning("topics table has no video_id column; falling back to global topic upsert")
                return self._upsert_global_topics(topics)
            raise

    def create_atom_topics(self, atom_topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建原子-话题关联"""
        if not atom_topics:
            return []

        if atom_topics and 'topic' in atom_topics[0] and 'topic_id' not in atom_topics[0]:
            topic_map = self._get_topic_ids_by_names([row.get('topic') for row in atom_topics])
            atom_topics = [
                {
                    'atom_id': row.get('atom_id'),
                    'topic_id': topic_map[row.get('topic')],
                    'relevance_score': row.get('relevance_score', 1.0),
                }
                for row in atom_topics
                if row.get('topic') in topic_map and row.get('atom_id')
            ]
            if not atom_topics:
                logger.warning("skip atom_topics persistence because no topic ids could be resolved")
                return []

        result = self.client.table('atom_topics').insert(atom_topics).execute()
        if result.data:
            logger.info(f"原子-话题关联创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"原子-话题关联创建失败: {result}")

    def replace_atom_topics(self, atom_ids: List[str], atom_topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace atom_topics rows for the given atoms."""
        if atom_ids:
            batch_size = 100
            for i in range(0, len(atom_ids), batch_size):
                self.client.table('atom_topics').delete().in_('atom_id', atom_ids[i:i + batch_size]).execute()
        if not atom_topics:
            return []
        return self.create_atom_topics(atom_topics)

    # ============================================================================
    # 向量管理
    # ============================================================================

    def create_atom_embeddings(self, embeddings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量创建原子向量"""
        if not embeddings:
            return []

        # 转换 numpy 数组为列表
        for emb in embeddings:
            if 'embedding' in emb and isinstance(emb['embedding'], np.ndarray):
                emb['embedding'] = emb['embedding'].tolist()

        result = self.client.table('atom_embeddings').upsert(embeddings, on_conflict='atom_id').execute()
        if result.data:
            logger.info(f"原子向量创建成功: {len(result.data)} 个")
            return result.data
        else:
            raise Exception(f"原子向量创建失败: {result}")

    def get_atom_embeddings_by_ids(self, atom_ids: List[str]) -> List[Dict[str, Any]]:
        """Load persisted embeddings for atoms."""
        if not atom_ids:
            return []
        result = self.client.table('atom_embeddings').select("*").in_('atom_id', atom_ids).execute()
        return result.data or []

    def search_atoms_by_vector(self, query_embedding: Union[List[float], np.ndarray],
                              match_threshold: float = 0.5, match_count: int = 10,
                              video_filter: str = None, project_filter: str = None) -> List[Dict[str, Any]]:
        """向量搜索原子"""
        if isinstance(query_embedding, np.ndarray):
            query_embedding = query_embedding.tolist()

        # 调用数据库函数
        result = self.client.rpc('search_atoms_by_vector', {
            'query_embedding': query_embedding,
            'match_threshold': match_threshold,
            'match_count': match_count,
            'video_filter': video_filter,
            'project_filter': project_filter
        }).execute()

        if result.data:
            logger.info(f"向量搜索完成: 找到 {len(result.data)} 个结果")
            return result.data
        return []

    # ============================================================================
    # 统计功能
    # ============================================================================

    def update_video_stats(self, video_id: str) -> bool:
        """更新视频统计信息"""
        try:
            result = self.client.rpc('update_video_stats', {'video_id_param': video_id}).execute()
        except Exception as e:
            if 'Could not find the function public.update_video_stats' in str(e):
                logger.warning("update_video_stats RPC missing in Supabase; skip stats refresh")
                return False
            raise
        if result.data is not None:  # 函数返回 void，所以 data 为 None 是正常的
            logger.info(f"视频统计更新完成: {video_id}")
            return True
        return False

    def get_video_stats(self, video_id: str) -> Optional[Dict[str, Any]]:
        """获取视频统计信息"""
        result = self.client.table('video_stats').select("*").eq('video_id', video_id).execute()
        return result.data[0] if result.data else None

    # ============================================================================
    # 批量操作和事务
    # ============================================================================

    def create_video_complete_data(self, video_data: Dict[str, Any]) -> bool:
        """
        完整创建视频及其所有关联数据
        包含：视频、片段、原子、标注、实体、话题、向量
        """
        try:
            video_id = video_data['video']['id']
            logger.info(f"开始创建完整视频数据: {video_id}")

            # 1. 创建视频记录
            video = self.create_video(**video_data['video'])

            # 2. 创建叙事片段
            if 'segments' in video_data and video_data['segments']:
                segments = self.create_narrative_segments(video_data['segments'])

            # 3. 创建原子
            if 'atoms' in video_data and video_data['atoms']:
                atoms = self.create_atoms(video_data['atoms'])

            # 4. 创建原子标注
            if 'atom_annotations' in video_data and video_data['atom_annotations']:
                annotations = self.create_atom_annotations(video_data['atom_annotations'])

            # 5. 创建实体
            if 'entities' in video_data and video_data['entities']:
                entities = self.create_entities(video_data['entities'])

            # 6. 创建原子-实体关联
            if 'atom_entities' in video_data and video_data['atom_entities']:
                atom_entities = self.create_atom_entities(video_data['atom_entities'])

            # 7. 创建话题
            if 'topics' in video_data and video_data['topics']:
                topics = self.create_topics(video_data['topics'])

            # 8. 创建原子-话题关联
            if 'atom_topics' in video_data and video_data['atom_topics']:
                atom_topics = self.create_atom_topics(video_data['atom_topics'])

            # 9. 创建向量
            if 'embeddings' in video_data and video_data['embeddings']:
                embeddings = self.create_atom_embeddings(video_data['embeddings'])

            # 10. 更新视频状态和统计
            self.update_video_status(video_id, 'completed', 'all_processing_complete')
            self.update_video_stats(video_id)

            logger.info(f"完整视频数据创建成功: {video_id}")
            return True

        except Exception as e:
            logger.error(f"完整视频数据创建失败: {video_id}, 错误: {e}")
            # 更新视频状态为错误
            try:
                self.update_video_status(video_id, 'error', f'creation_failed: {str(e)[:100]}')
            except:
                pass
            raise e

    # ============================================================================
    # 实用工具方法
    # ============================================================================

    def check_connection(self) -> bool:
        """检查数据库连接"""
        try:
            result = self.client.table('projects').select("id").limit(1).execute()
            logger.info("数据库连接正常")
            return True
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            return False

    def get_database_info(self) -> Dict[str, Any]:
        """获取数据库信息统计"""
        info = {}

        tables = ['projects', 'videos', 'atoms', 'entities', 'topics', 'atom_embeddings']

        for table in tables:
            try:
                result = self.client.table(table).select("id", count="exact").execute()
                info[table] = result.count
            except Exception as e:
                info[table] = f"error: {e}"

        return info
