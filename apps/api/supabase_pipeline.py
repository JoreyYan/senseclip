"""
Supabase 集成的视频处理流水线
从字幕文件到 Supabase 数据库的完整处理流程
"""

import logging
import tempfile
import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from types import SimpleNamespace

# 导入现有的处理器
from analyzers.atom_annotator import AtomAnnotator
from analyzers.entity_extractor import EntityExtractor
from analyzers.deep_analyzer import DeepAnalyzer
from structurers.segment_identifier import SegmentIdentifier
from vectorizers import OpenAIVectorizer
from database import SupabaseClient
from database.data_converter import DataConverter
from config import SUPABASE_URL, SUPABASE_KEY, CLAUDE_API_KEY, OPENAI_API_KEY

logger = logging.getLogger(__name__)


class SupabasePipeline:
    """集成 Supabase 的视频处理流水线"""

    def __init__(self, project_id: Optional[str] = None):
        """
        初始化流水线

        Args:
            project_id: 目标项目ID，如果为空则创建默认项目
        """
        self.project_id = project_id

        # 初始化数据库客户端
        self.db_client = SupabaseClient(SUPABASE_URL, SUPABASE_KEY)

        # 初始化数据转换器
        self.data_converter = DataConverter()

        # 保存 API key 供 atomizer 使用
        self.claude_api_key = CLAUDE_API_KEY

        # 初始化处理器
        self.atom_annotator = AtomAnnotator(CLAUDE_API_KEY)
        # EntityExtractor 是纯规则/聚合逻辑，不需要 LLM key
        self.entity_extractor = EntityExtractor()
        from config import DEEPSEEK_API_KEY
        self.deep_analyzer = DeepAnalyzer(CLAUDE_API_KEY, deepseek_api_key=DEEPSEEK_API_KEY)
        self.segment_identifier = SegmentIdentifier(CLAUDE_API_KEY)
        from vectorizers import create_vectorizer
        self.vectorizer = create_vectorizer()
        self._progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None

        # 确保项目存在
        self._ensure_project()

        logger.info(f"SupabasePipeline 初始化完成，项目ID: {self.project_id}")

    def _emit_progress(self, step: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self._progress_callback:
            return
        try:
            self._progress_callback(step, payload or {})
        except Exception:
            logger.debug("progress callback failed for step=%s", step, exc_info=True)

    def _ensure_video_record(self, video_id: str, video_title: str, duration_ms: int, srt_file_path: str) -> None:
        self.db_client.upsert_video({
            'id': video_id,
            'title': video_title,
            'duration_ms': duration_ms,
            'duration_seconds': duration_ms // 1000,
            'srt_file_path': srt_file_path,
            'status': 'processing',
            'processing_stage': 'subtitle_uploaded',
            'atom_count': 0,
            'segment_count': 0,
            'entity_count': 0,
        })
        if self.project_id:
            self.db_client.add_video_to_project(self.project_id, video_id)

    def _normalize_db_atoms(self, atoms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for atom in atoms:
            normalized.append({
                'atom_id': atom.get('id'),
                'start_ms': atom.get('start_ms'),
                'end_ms': atom.get('end_ms'),
                'duration_ms': atom.get('duration_ms'),
                'merged_text': atom.get('merged_text'),
                'start_time': atom.get('start_time'),
                'end_time': atom.get('end_time'),
                'duration_seconds': atom.get('duration_seconds'),
                'type': atom.get('type'),
                'completeness': atom.get('completeness'),
                'source_utterance_ids': atom.get('source_utterance_ids', []),
                'segment_id': atom.get('segment_id'),
            })
        return normalized

    def _load_persisted_segments(self, video_id: str, atoms_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        segments = self.db_client.get_segments_by_video(video_id)
        if not segments:
            return []

        atoms_by_segment: Dict[str, List[str]] = {}
        for atom in atoms_data:
            segment_id = atom.get('segment_id')
            atom_id = atom.get('atom_id')
            if segment_id and atom_id:
                atoms_by_segment.setdefault(segment_id, []).append(atom_id)

        hydrated = []
        for seg in segments:
            segment_id = seg.get('id')
            hydrated.append({
                'segment_id': segment_id,
                'segment_num': int(str(segment_id).split('_')[-1]) if segment_id else None,
                'start_ms': seg.get('start_ms'),
                'end_ms': seg.get('end_ms'),
                'duration_ms': seg.get('duration_ms'),
                'title': seg.get('title'),
                'summary': seg.get('summary'),
                'atom_count': seg.get('atom_count', 0),
                'topics': seg.get('topics', []),
                'key_entities': seg.get('key_entities', []),
                'narrative_type': seg.get('narrative_type'),
                'atoms': atoms_by_segment.get(segment_id, []),
            })
        return hydrated

    def _load_persisted_annotations(self, atom_ids: List[str]) -> List[Dict[str, Any]]:
        annotations_by_atom = self.db_client.get_atom_annotations_by_ids(atom_ids)
        entities_by_atom = self.db_client.get_atom_entities_by_ids(atom_ids)
        annotations: List[Dict[str, Any]] = []
        for atom_id in atom_ids:
            row = annotations_by_atom.get(atom_id)
            if not row:
                continue
            annotations.append({
                'atom_id': atom_id,
                'topics': row.get('topics', []) or [],
                'entities': [
                    {
                        'name': entity.get('entity_name'),
                        'type': entity.get('entity_type'),
                        'confidence': entity.get('confidence', 0.0),
                    }
                    for entity in entities_by_atom.get(atom_id, [])
                ],
                'emotion': {
                    'type': row.get('emotion_type'),
                    'score': row.get('emotion_score'),
                    'confidence': row.get('emotion_confidence'),
                    'distribution': row.get('emotion_distribution'),
                },
                'importance_score': row.get('importance_score'),
                'has_entity': row.get('has_entity', False),
                'has_topic': row.get('has_topic', False),
                'embedding_status': row.get('embedding_status', 'pending'),
                'parent_segment_id': row.get('parent_segment_id'),
                'parent_narrative_id': row.get('parent_narrative_id'),
            })
        return annotations

    def _load_persisted_entities(self, video_id: str) -> Dict[str, Any]:
        rows = self.db_client.get_entities_by_video(video_id)
        if not rows:
            return {'entities': []}
        return {
            'entities': [
                {
                    'entity_id': row.get('id'),
                    'name': row.get('name'),
                    'entity_type': row.get('entity_type'),
                    'description': row.get('description'),
                    'aliases': row.get('aliases', []),
                    'mention_count': row.get('mention_count', 0),
                    'importance_score': row.get('importance_score'),
                    'first_mention_ms': row.get('first_mention_ms'),
                    'last_mention_ms': row.get('last_mention_ms'),
                    'mentioned_in_atoms': row.get('mentioned_in_atoms', []),
                }
                for row in rows
            ]
        }

    def _load_persisted_topics(self, video_id: str) -> Dict[str, Any]:
        rows = self.db_client.get_topics_by_video(video_id)
        if not rows:
            return {'primary_topics': []}
        return {
            'primary_topics': [
                {
                    'topic': row.get('topic'),
                    'weight': row.get('weight'),
                    'segments': row.get('segments', []),
                    'atoms': row.get('atoms', []),
                }
                for row in rows
            ]
        }

    def _persist_atoms(self, video_id: str, atoms_data: List[Dict[str, Any]], segments_data: Optional[List[Any]] = None) -> None:
        atom_rows = self.data_converter._convert_atoms_data(atoms_data, video_id, segments_data or [])
        self.db_client.create_atoms(atom_rows)
        self.db_client.update_video_status(video_id, 'processing', 'atomized')

    def _persist_segments(self, video_id: str, processing_results: Dict[str, Any]) -> None:
        segment_rows = self.data_converter._convert_segments_data(
            processing_results.get('narrative_segments', []),
            video_id,
        )
        self.db_client.create_narrative_segments(segment_rows)
        self._persist_atoms(video_id, processing_results.get('atoms', []), processing_results.get('narrative_segments', []))
        self.db_client.update_video_status(video_id, 'processing', 'segmented')

    def _persist_entity_stage(self, video_id: str, processing_results: Dict[str, Any]) -> None:
        annotations = processing_results.get('atom_annotations', [])
        self.db_client.save_atom_annotations(annotations, video_id=video_id)
        entity_rows = self.data_converter._convert_entities_data(processing_results.get('entities', {}), video_id)
        self.db_client.replace_entities(video_id, entity_rows)
        # Keep videos.entity_count in sync so library stats are accurate
        try:
            self.db_client.client.table('videos').update(
                {'entity_count': len(entity_rows)}
            ).eq('id', video_id).execute()
        except Exception:
            pass
        # Persist structured relation events for Q&A use
        relation_events = processing_results.get('relation_events') or []
        if relation_events:
            try:
                import json as _json
                self.db_client.client.table('app_settings').upsert({
                    'key': f'video_relation_events_v1_{video_id}',
                    'value': _json.dumps(relation_events, ensure_ascii=False),
                }).execute()
            except Exception:
                pass
        self.db_client.update_video_status(video_id, 'processing', 'entities_extracted')

    def _persist_topic_stage(self, video_id: str, processing_results: Dict[str, Any]) -> None:
        atom_ids = [annotation.get('atom_id') for annotation in processing_results.get('atom_annotations', []) if annotation.get('atom_id')]
        topic_rows = self.data_converter._convert_topics_data(processing_results.get('topics', {}), video_id)
        atom_topic_rows = self.data_converter._convert_atom_topics_data(
            processing_results.get('atom_annotations', []),
            processing_results.get('topics', {}),
        )
        self.db_client.replace_topics(video_id, topic_rows)
        self.db_client.replace_atom_topics(atom_ids, atom_topic_rows)
        self.db_client.update_video_status(video_id, 'processing', 'topics_extracted')

    def _persist_embeddings(self, processing_results: Dict[str, Any], video_id: str) -> None:
        embedding_rows = self.data_converter._convert_embeddings_data(processing_results.get('embeddings', {}), video_id=video_id)
        self.db_client.create_atom_embeddings(embedding_rows)
        self.db_client.update_video_status(video_id, 'processing', 'vectorized')

    def _ensure_project(self):
        """确保项目存在"""
        if self.project_id:
            project = self.db_client.get_project(self.project_id)
            if not project:
                raise ValueError(f"项目不存在: {self.project_id}")
        else:
            # 创建默认项目
            project = self.db_client.create_project(
                title=f"视频处理项目 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                description="通过 SupabasePipeline 创建的项目"
            )
            self.project_id = project['id']
            logger.info(f"创建默认项目: {self.project_id}")

    def process_subtitle_file(
        self,
        srt_file_path: str,
        video_id: str = None,
        video_title: str = None,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        force_reprocess: bool = False,
    ) -> Dict[str, Any]:
        """
        处理字幕文件的完整流程

        Args:
            srt_file_path: 字幕文件路径
            video_id: 视频ID，如果为空则自动生成
            video_title: 视频标题，如果为空则从文件名生成
            force_reprocess: 强制重新处理，忽略已有原子/片段/实体缓存

        Returns:
            Dict: 处理结果，包含视频ID和统计信息
        """
        if force_reprocess and video_id:
            logger.info(f"force_reprocess=True，清除 {video_id} 的已有数据")
            try:
                # Step 1: get all atom IDs for this video (both prefixed and legacy formats)
                existing = self.db_client.supabase.table("atoms").select("id").eq("video_id", video_id).execute()
                atom_ids = [r["id"] for r in (existing.data or []) if r.get("id")]
                logger.info(f"  找到 {len(atom_ids)} 个旧原子，逐步清除依赖数据")

                # Step 2: delete dependent tables by atom_id (reliable, no NULL issues)
                batch = 200
                for i in range(0, len(atom_ids), batch):
                    chunk = atom_ids[i:i+batch]
                    self.db_client.supabase.table("atom_embeddings").delete().in_("atom_id", chunk).execute()
                    self.db_client.supabase.table("atom_annotations").delete().in_("atom_id", chunk).execute()
                    self.db_client.supabase.table("atom_entities").delete().in_("atom_id", chunk).execute()
                    self.db_client.supabase.table("atom_topics").delete().in_("atom_id", chunk).execute()

                # Step 3: now safe to delete atoms themselves
                self.db_client.supabase.table("atoms").delete().eq("video_id", video_id).execute()

                # Step 4: delete video-level tables
                self.db_client.supabase.table("narrative_segments").delete().eq("video_id", video_id).execute()
                self.db_client.supabase.table("entities").delete().eq("video_id", video_id).execute()
                self.db_client.supabase.table("person_relations").delete().eq("video_id", video_id).execute()
                logger.info(f"已清除 {video_id} 旧数据（含 person_relations），重新处理")
            except Exception as e:
                logger.warning(f"清除旧数据时出错（继续）: {e}")
        srt_path = Path(srt_file_path)
        if not srt_path.exists():
            raise FileNotFoundError(f"字幕文件不存在: {srt_file_path}")

        # 生成视频ID和标题
        if not video_id:
            video_id = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if not video_title:
            video_title = srt_path.stem

        logger.info(f"开始处理字幕文件: {srt_file_path}")
        logger.info(f"视频ID: {video_id}, 标题: {video_title}")

        current_step = "starting"
        self._progress_callback = progress_callback
        try:
            self._emit_progress("starting", {"message": "开始分析流程"})

            # 步骤1: 解析字幕和原子化
            current_step = "atomized"
            processing_results = self._process_subtitles_to_atoms(srt_file_path, video_id, video_title, force_reprocess=force_reprocess)
            self._persist_atoms(video_id, processing_results.get("atoms", []))
            self._emit_progress("atomized", {
                "atoms_count": len(processing_results.get("atoms", []))
            })

            # 步骤2: 片段识别
            current_step = "segmented"
            processing_results = self._identify_narrative_segments(processing_results, video_id)
            self._persist_segments(video_id, processing_results)
            self._emit_progress("segmented", {
                "segments_count": len(processing_results.get("narrative_segments", []))
            })

            # 步骤3: 实体提取和标注
            current_step = "entities_extracted"
            processing_results = self._extract_entities_and_annotate(processing_results, video_id)
            self._persist_entity_stage(video_id, processing_results)
            entities_data = processing_results.get("entities", {})
            entities_count = len(entities_data.get("entities", [])) if isinstance(entities_data, dict) else 0
            self._emit_progress("entities_extracted", {
                "entities_count": entities_count
            })

            # 步骤4: 话题提取
            current_step = "topics_extracted"
            processing_results = self._extract_topics(processing_results, video_id)
            self._persist_topic_stage(video_id, processing_results)
            self._emit_progress("topics_extracted", {})

            # 步骤5: 向量化
            current_step = "vectorized"
            processing_results = self._vectorize_atoms(processing_results, video_id)
            self._persist_embeddings(processing_results, video_id)
            self._emit_progress("vectorized", {
                "embeddings_count": len(processing_results.get("embeddings", {}))
            })

            current_step = "processed"
            self.db_client.update_video_status(video_id, 'completed', 'all_processing_complete')
            self.db_client.update_video_stats(video_id)
            result = {
                'success': True,
                'video_id': video_id,
                'project_id': self.project_id,
                'stats': self.db_client.get_video_stats(video_id),
                'atoms_count': len(processing_results.get("atoms", [])),
                'segments_count': len(processing_results.get("narrative_segments", [])),
                'entities_count': len(processing_results.get("entities", {}).get("entities", [])),
                'embeddings_count': len(processing_results.get("embeddings", {})),
            }
            self._emit_progress("processed", {
                "video_id": result.get("video_id"),
                "atoms_count": result.get("atoms_count", 0),
                "segments_count": result.get("segments_count", 0),
                "entities_count": result.get("entities_count", 0),
                "embeddings_count": result.get("embeddings_count", 0),
            })

            logger.info(f"字幕文件处理完成: {video_id}")
            return result

        except Exception as e:
            logger.error(f"字幕文件处理失败: {video_id}, 错误: {e}")
            self._emit_progress("error", {"failed_step": current_step, "error_message": str(e)})
            # 更新视频状态为错误
            try:
                self.db_client.update_video_status(video_id, 'error', f'processing_failed: {str(e)[:100]}')
            except:
                pass
            raise e
        finally:
            self._progress_callback = None

    def _process_subtitles_to_atoms(self, srt_file_path: str, video_id: str, video_title: str, force_reprocess: bool = False) -> Dict[str, Any]:
        """步骤1: 解析字幕并进行原子化"""
        logger.info("步骤1: 解析字幕并原子化")

        from parsers.srt_parser import SRTParser
        from atomizers.atomizer import Atomizer

        # 解析 SRT → Utterance 列表
        parser = SRTParser()
        utterances = parser.parse(srt_file_path)
        logger.info(f"解析字幕完成，共 {len(utterances)} 条")

        # 计算总时长
        duration_ms = utterances[-1].end_ms if utterances else 0
        self._ensure_video_record(video_id, video_title, duration_ms, srt_file_path)

        existing_atoms = self.db_client.get_atoms_by_video(video_id)
        if existing_atoms and not force_reprocess:
            logger.info(f"复用已持久化原子: {len(existing_atoms)}")
            return {
                'atoms': self._normalize_db_atoms(existing_atoms),
                'duration_ms': duration_ms,
                'srt_file_path': srt_file_path
            }

        # 原子化（用 video_id 作为 checkpoint_id，支持断点续传）
        from config import XAI_API_KEY, DEEPSEEK_API_KEY
        _batch_size = 50
        atomizer = Atomizer(
            api_key=self.claude_api_key,
            batch_size=_batch_size,
            prompt_version='v1',
            use_cache=True,
            checkpoint_id=video_id,
            xai_api_key=XAI_API_KEY,
            deepseek_api_key=DEEPSEEK_API_KEY,
        )
        atoms = atomizer.atomize(
            utterances,
            progress_callback=lambda payload: self._emit_progress("atomizing_progress", payload),
        )
        logger.info(f"原子化完成，共 {len(atoms)} 个原子")

        # 转为 dict 列表（后续步骤期望的格式）
        atoms_data = [atom.model_dump() for atom in atoms]

        # 加视频前缀，确保 atom_id 全局唯一（防止不同视频的 A001 在 upsert 时互相覆盖）
        for atom in atoms_data:
            raw_id = atom.get('atom_id', '')
            if raw_id and not raw_id.startswith(f"{video_id}_"):
                atom['atom_id'] = f"{video_id}_{raw_id}"

        return {
            'atoms': atoms_data,
            'duration_ms': duration_ms,
            'srt_file_path': srt_file_path
        }

    def _identify_narrative_segments(self, processing_results: Dict[str, Any],
                                   video_id: str) -> Dict[str, Any]:
        """步骤2: 使用 AI 语义分片识别叙事片段"""
        logger.info("步骤2: AI 语义分片")

        atoms_data = processing_results.get('atoms', [])
        if not atoms_data:
            processing_results['narrative_segments'] = []
            return processing_results

        existing_segments = self._load_persisted_segments(video_id, atoms_data)
        if existing_segments:
            logger.info(f"复用已持久化片段: {len(existing_segments)}")
            processing_results['narrative_segments'] = existing_segments
            return processing_results

        from structurers.semantic_segmenter import SemanticSegmenter
        from models.narrative_segment import SegmentMeta

        # 准备 SemanticSegmenter 的输入格式
        # atoms 需要 id, merged_text, start_ms, end_ms
        seg_atoms = []
        for a in sorted(atoms_data, key=lambda x: x.get('start_ms', 0)):
            seg_atoms.append({
                "id": a.get("atom_id") or a.get("id", ""),
                "merged_text": a.get("merged_text", ""),
                "start_ms": a.get("start_ms", 0),
                "end_ms": a.get("end_ms", 0),
            })

        if not seg_atoms:
            processing_results['narrative_segments'] = []
            return processing_results

        # 加载 atom_entities（如果 pipeline 已经提取过的话，这里从 DB 拉）
        all_entities = []
        try:
            atom_ids = [a["id"] for a in seg_atoms]
            for i in range(0, len(atom_ids), 200):
                chunk = atom_ids[i:i+200]
                ent_res = self.db_client.supabase.table("atom_entities") \
                    .select("atom_id,entity_name,entity_type") \
                    .in_("atom_id", chunk).execute()
                all_entities.extend(ent_res.data or [])
        except Exception as ex:
            logger.warning(f"加载 atom_entities 失败（可能尚未提取）: {ex}")

        # 加载别名
        alias_map: Dict[str, str] = {}
        try:
            for key in ("person_network_aliases_v1", "entity_aliases_v1"):
                row = self.db_client.supabase.table("app_settings").select("value").eq("key", key).execute()
                if row.data:
                    import json as _json
                    raw = row.data[0].get("value", {})
                    if isinstance(raw, str):
                        raw = _json.loads(raw)
                    for alias, val in raw.items():
                        canonical = val if isinstance(val, str) else val.get("canonical", "")
                        if canonical:
                            alias_map[alias] = canonical
        except Exception:
            pass

        if alias_map:
            for e in all_entities:
                name = e.get("entity_name", "")
                if name in alias_map:
                    e["entity_name"] = f"{alias_map[name]}({name})"

        # 调用 SemanticSegmenter
        from config import XAI_API_KEY as _xai_key, DEEPSEEK_API_KEY as _ds_key
        segmenter = SemanticSegmenter(CLAUDE_API_KEY, xai_api_key=_xai_key, deepseek_api_key=_ds_key)
        segments = segmenter.segment(seg_atoms, all_entities)

        # 转换为 SegmentMeta 格式（pipeline 下游兼容）
        final_segments = []
        for seg in segments:
            final_segments.append(SegmentMeta(
                segment_num=seg["segment_num"],
                atoms=seg["atom_ids"],
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                duration_ms=seg["end_ms"] - seg["start_ms"],
                reason=seg.get("title", ""),
                confidence=0.9,
                title=seg.get("title"),
                summary=seg.get("summary"),
                key_entities=seg.get("core_persons"),
                topics=seg.get("keywords"),
            ))

        logger.info(f"AI 语义分片完成: {len(final_segments)} 个片段，覆盖 {sum(len(s.atoms) for s in final_segments)} 个原子")
        processing_results['narrative_segments'] = final_segments
        return processing_results

    def _extract_entities_and_annotate(self, processing_results: Dict[str, Any],
                                     video_id: str) -> Dict[str, Any]:
        """步骤3: 实体提取和原子标注"""
        logger.info("步骤3: 实体提取和原子标注")

        atoms_data = processing_results.get('atoms', [])
        if not atoms_data:
            processing_results['entities'] = {'entities': []}
            processing_results['atom_annotations'] = []
            return processing_results

        atom_ids = [atom.get('atom_id') for atom in atoms_data if atom.get('atom_id')]
        persisted_annotations = self._load_persisted_annotations(atom_ids)
        if len(persisted_annotations) == len(atom_ids) and atom_ids:
            logger.info(f"复用已持久化原子注释: {len(persisted_annotations)}")
            processing_results['atom_annotations'] = persisted_annotations
            persisted_entities = self._load_persisted_entities(video_id)
            if persisted_entities.get('entities'):
                logger.info(f"复用已持久化实体: {len(persisted_entities.get('entities', []))}")
                processing_results['entities'] = persisted_entities
                return processing_results

            # If entity rows are missing but annotations exist, rebuild cheaply from annotations.
            processing_results['entities'] = self._aggregate_entities_from_annotations(persisted_annotations)
            return processing_results

        # 原子标注（并发：每个原子一次 LLM 调用，纯网络等待，串行会拖垮长视频）
        import os as _os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        annotate_concurrency = max(1, int(_os.environ.get("ANNOTATE_CONCURRENCY", "6")))

        total_atoms = len(atoms_data)
        self._emit_progress("entities_progress", {
            "phase": "annotating_atoms",
            "done": 0,
            "total": total_atoms,
        })

        def _normalize_annotation(annotation, atom):
            if hasattr(annotation, 'to_dict'):
                return annotation.to_dict()
            if isinstance(annotation, dict):
                return annotation
            if hasattr(annotation, 'model_dump'):
                return annotation.model_dump()
            return {
                'atom_id': getattr(annotation, 'atom_id', atom.get('atom_id')),
                'entities': getattr(annotation, 'entities', []) or [],
                'topics': getattr(annotation, 'topics', []) or [],
            }

        annotations: List[Any] = [None] * total_atoms
        done_count = 0
        with ThreadPoolExecutor(max_workers=annotate_concurrency) as pool:
            future_to_idx = {
                pool.submit(self.atom_annotator.annotate_atom, atom): idx
                for idx, atom in enumerate(atoms_data)
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                annotations[idx] = _normalize_annotation(fut.result(), atoms_data[idx])
                done_count += 1
                if done_count % 10 == 0 or done_count == total_atoms:
                    self._emit_progress("entities_progress", {
                        "phase": "annotating_atoms",
                        "done": done_count,
                        "total": total_atoms,
                    })

        # 实体提取
        entities = None
        segments_data = processing_results.get('narrative_segments', [])
        if segments_data:
            try:
                entities = self._extract_entities_via_segments(segments_data, atoms_data)
            except Exception as e:
                logger.warning(f"segment-based entity extraction failed, fallback to annotation aggregation: {e}")

        if entities is None:
            if hasattr(self.entity_extractor, 'extract_entities'):
                entities = self.entity_extractor.extract_entities(atoms_data, annotations)
            else:
                entities = self._aggregate_entities_from_annotations(annotations)

        processing_results['atom_annotations'] = annotations
        processing_results['entities'] = entities
        return processing_results

    def _extract_entities_via_segments(self, segments_data: List[Any], atoms_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run segment-level entity extraction, then normalize with EntityExtractor.extract()."""
        if not segments_data or not atoms_data:
            return {'entities': []}

        atom_by_id = {atom.get('atom_id'): atom for atom in atoms_data if atom.get('atom_id')}
        pseudo_segments = []
        all_relation_events: List[Dict] = []

        total_segments = len(segments_data)
        self._emit_progress("entities_progress", {
            "phase": "extracting_segments",
            "done": 0,
            "total": total_segments,
        })
        for idx, seg in enumerate(segments_data, start=1):
            seg_atoms = getattr(seg, 'atoms', None)
            if seg_atoms is None and isinstance(seg, dict):
                seg_atoms = seg.get('atoms', [])
            seg_atoms = seg_atoms or []

            resolved_atom_ids: List[str] = []
            texts: List[str] = []
            for atom_ref in seg_atoms:
                atom = None
                if isinstance(atom_ref, int):
                    if 0 <= atom_ref < len(atoms_data):
                        atom = atoms_data[atom_ref]
                else:
                    atom = atom_by_id.get(atom_ref)

                if atom:
                    atom_id = atom.get('atom_id')
                    if atom_id:
                        resolved_atom_ids.append(atom_id)
                    text = atom.get('merged_text') or ''
                    if text:
                        texts.append(text)

            if not texts:
                continue

            ai_result = self.deep_analyzer.analyze_segment_entities("\n".join(texts)) or {}
            ai_entities = ai_result.get('entities', {}) if isinstance(ai_result, dict) else {}

            # Collect structured relation events (who+action+target+time) for Q&A use
            seg_relation_events = list(ai_result.get('relation_events', []) or [])
            for rev in seg_relation_events:
                if isinstance(rev, dict):
                    rev['atom_ids'] = resolved_atom_ids[:3]
            all_relation_events.extend(seg_relation_events)

            countries = list(ai_entities.get('countries', []) or [])
            locations = list(ai_entities.get('locations', []) or [])
            if locations:
                countries = list(dict.fromkeys(countries + locations))

            entities_obj = SimpleNamespace(
                persons=list(ai_entities.get('persons', []) or []),
                countries=countries,
                organizations=list(ai_entities.get('organizations', []) or []),
                time_points=list(ai_entities.get('time_points', []) or []),
                events=list(ai_entities.get('events', []) or []),
                concepts=list(ai_entities.get('concepts', []) or []),
            )

            seg_num = getattr(seg, 'segment_num', None)
            if seg_num is None and isinstance(seg, dict):
                seg_num = seg.get('segment_num')
            segment_id = f"SEG_{int(seg_num):03d}" if isinstance(seg_num, int) else f"SEG_{len(pseudo_segments) + 1:03d}"

            pseudo_segments.append(
                SimpleNamespace(
                    segment_id=segment_id,
                    atoms=resolved_atom_ids or seg_atoms,
                    entities=entities_obj,
                    topics=SimpleNamespace(primary_topic=None),
                )
            )
            if idx % 5 == 0 or idx == total_segments:
                self._emit_progress("entities_progress", {
                    "phase": "extracting_segments",
                    "done": idx,
                    "total": total_segments,
                })

        if not pseudo_segments:
            return {'entities': []}

        normalized = self.entity_extractor.extract(pseudo_segments, atoms_data)
        result = self._convert_entity_extractor_output(normalized)
        result['relation_events'] = all_relation_events
        return result

    def _convert_entity_extractor_output(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        """Convert EntityExtractor.extract() output to DataConverter-compatible payload."""
        if not isinstance(normalized, dict):
            return {'entities': []}

        entities: List[Dict[str, Any]] = []
        for entity_type in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']:
            for item in normalized.get(entity_type, []) or []:
                name = (item.get('name') or '').strip()
                if not name:
                    continue

                entities.append({
                    'entity_id': f'{entity_type}:{name}',
                    'name': name,
                    'entity_type': entity_type,
                    'description': None,
                    'aliases': [],
                    'mention_count': int(item.get('mentions', 0) or 0),
                    'importance_score': None,
                    'first_mention_ms': None,
                    'last_mention_ms': None,
                    'mentioned_in_atoms': list(item.get('atoms', []) or []),
                })

        entities.sort(key=lambda x: x['mention_count'], reverse=True)
        return {'entities': entities}

    def _aggregate_entities_from_annotations(self, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build entities payload from atom annotations when extractor API is unavailable."""
        if not annotations:
            return {'entities': []}

        type_alias = {
            'person': 'persons',
            'persons': 'persons',
            'country': 'countries',
            'countries': 'countries',
            'location': 'countries',
            'locations': 'countries',
            'organization': 'organizations',
            'organizations': 'organizations',
            'time_point': 'time_points',
            'time_points': 'time_points',
            'event': 'events',
            'events': 'events',
            'concept': 'concepts',
            'concepts': 'concepts',
        }

        aggregated: Dict[tuple[str, str], Dict[str, Any]] = {}
        for annotation in annotations:
            atom_id = annotation.get('atom_id')
            for entity in annotation.get('entities', []) or []:
                name = (entity.get('name') or '').strip()
                raw_type = (entity.get('type') or 'concepts').strip().lower()
                if not name:
                    continue

                entity_type = type_alias.get(raw_type, raw_type if raw_type.endswith('s') else f'{raw_type}s')
                key = (entity_type, name)
                if key not in aggregated:
                    aggregated[key] = {
                        'entity_id': f'{entity_type}:{name}',
                        'name': name,
                        'entity_type': entity_type,
                        'description': None,
                        'aliases': [],
                        'mention_count': 0,
                        'importance_score': None,
                        'first_mention_ms': None,
                        'last_mention_ms': None,
                        'mentioned_in_atoms': [],
                    }

                aggregated[key]['mention_count'] += 1
                if atom_id and atom_id not in aggregated[key]['mentioned_in_atoms']:
                    aggregated[key]['mentioned_in_atoms'].append(atom_id)

        entities = sorted(aggregated.values(), key=lambda item: item['mention_count'], reverse=True)
        return {'entities': entities}

    def _extract_topics(self, processing_results: Dict[str, Any], video_id: str) -> Dict[str, Any]:
        """步骤4: 话题提取"""
        logger.info("步骤4: 话题提取")

        atoms_data = processing_results.get('atoms', [])
        segments_data = processing_results.get('narrative_segments', [])

        if not atoms_data:
            processing_results['topics'] = {'primary_topics': []}
            return processing_results

        persisted_topics = self._load_persisted_topics(video_id)
        if persisted_topics.get('primary_topics'):
            logger.info(f"复用已持久化话题: {len(persisted_topics.get('primary_topics', []))}")
            processing_results['topics'] = persisted_topics
            return processing_results

        # Prefer rebuilding from persisted annotations to avoid repeated token usage.
        if processing_results.get('atom_annotations'):
            topics = self._extract_topics_from_annotations(
                processing_results.get('atom_annotations', []),
                segments_data,
                atoms_data,
            )
        elif hasattr(self.deep_analyzer, 'extract_topics'):
            topics = self.deep_analyzer.extract_topics(atoms_data, segments_data)
        else:
            topics = self._extract_topics_from_annotations(
                processing_results.get('atom_annotations', []),
                segments_data,
                atoms_data,
            )

        processing_results['topics'] = topics
        return processing_results

    def _extract_topics_from_annotations(
        self,
        annotations: List[Dict[str, Any]],
        segments_data: List[Any],
        atoms_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build topics payload from atom annotations when deep analyzer topic API is unavailable."""
        if not annotations:
            return {'primary_topics': []}

        atom_to_segment: Dict[str, str] = {}
        for idx, seg in enumerate(segments_data or []):
            seg_num = getattr(seg, 'segment_num', None)
            if seg_num is None and isinstance(seg, dict):
                seg_num = seg.get('segment_num')
            segment_id = f"SEG_{int(seg_num):03d}" if isinstance(seg_num, int) else f"SEG_{idx + 1:03d}"

            seg_atoms = getattr(seg, 'atoms', None)
            if seg_atoms is None and isinstance(seg, dict):
                seg_atoms = seg.get('atoms', [])
            for atom_ref in seg_atoms or []:
                if isinstance(atom_ref, int):
                    if 0 <= atom_ref < len(atoms_data):
                        atom_id = atoms_data[atom_ref].get('atom_id')
                        if atom_id:
                            atom_to_segment[atom_id] = segment_id
                else:
                    atom_to_segment[str(atom_ref)] = segment_id

        topic_agg: Dict[str, Dict[str, Any]] = {}
        total_mentions = 0
        for ann in annotations:
            atom_id = ann.get('atom_id')
            for topic in ann.get('topics', []) or []:
                topic_name = str(topic).strip()
                if not topic_name:
                    continue
                if topic_name not in topic_agg:
                    topic_agg[topic_name] = {'count': 0, 'atoms': set(), 'segments': set()}
                topic_agg[topic_name]['count'] += 1
                total_mentions += 1
                if atom_id:
                    topic_agg[topic_name]['atoms'].add(atom_id)
                    seg_id = atom_to_segment.get(atom_id)
                    if seg_id:
                        topic_agg[topic_name]['segments'].add(seg_id)

        if not topic_agg:
            return {'primary_topics': []}

        primary_topics = []
        for topic_name, payload in topic_agg.items():
            weight = payload['count'] / total_mentions if total_mentions > 0 else 0.0
            primary_topics.append({
                'topic': topic_name,
                'weight': round(weight, 6),
                'segments': sorted(payload['segments']),
                'atoms': sorted(payload['atoms']),
            })

        primary_topics.sort(key=lambda x: x['weight'], reverse=True)
        return {'primary_topics': primary_topics}

    def _vectorize_atoms(self, processing_results: Dict[str, Any], video_id: str) -> Dict[str, Any]:
        """步骤5: 原子向量化"""
        logger.info("步骤5: 原子向量化")

        atoms_data = processing_results.get('atoms', [])
        if not atoms_data:
            processing_results['embeddings'] = {}
            return processing_results

        atom_ids = [atom.get('atom_id') for atom in atoms_data if atom.get('atom_id')]
        existing_embeddings = self.db_client.get_atom_embeddings_by_ids(atom_ids)
        if len(existing_embeddings) == len(atom_ids) and atom_ids:
            logger.info(f"复用已持久化向量: {len(existing_embeddings)}")
            processing_results['embeddings'] = {
                item.get('atom_id'): item.get('embedding') for item in existing_embeddings if item.get('atom_id')
            }
            return processing_results

        # 向量化所有原子
        embeddings = {}
        total_atoms = len(atoms_data)
        self._emit_progress("vectorizing_progress", {"done": 0, "total": total_atoms})
        for idx, atom in enumerate(atoms_data, start=1):
            atom_id = atom.get('atom_id')
            text = atom.get('merged_text', '')

            if text:
                embedding = self.vectorizer.vectorize_text(text)
                embeddings[atom_id] = embedding
            if idx % 10 == 0 or idx == total_atoms:
                self._emit_progress("vectorizing_progress", {"done": idx, "total": total_atoms})

        processing_results['embeddings'] = embeddings
        return processing_results

    def _convert_to_supabase_format(self, video_id: str, video_title: str,
                                   processing_results: Dict[str, Any],
                                   srt_file_path: str) -> Dict[str, Any]:
        """步骤6: 转换为 Supabase 格式"""
        logger.info("步骤6: 转换为 Supabase 格式")

        processing_results['srt_file_path'] = srt_file_path
        return self.data_converter.convert_processing_results_to_supabase(
            video_id, video_title, processing_results
        )

    def _save_to_supabase(self, supabase_data: Dict[str, Any]) -> Dict[str, Any]:
        """步骤7: 保存到 Supabase"""
        logger.info("步骤7: 保存到 Supabase")

        # 创建完整的视频数据
        success = self.db_client.create_video_complete_data(supabase_data)

        if success:
            video_id = supabase_data['video']['id']

            # 将视频添加到项目
            if self.project_id:
                self.db_client.add_video_to_project(self.project_id, video_id)

            # 获取统计信息
            stats = self.db_client.get_video_stats(video_id)

            return {
                'success': True,
                'video_id': video_id,
                'project_id': self.project_id,
                'stats': stats,
                'atoms_count': len(supabase_data.get('atoms', [])),
                'segments_count': len(supabase_data.get('segments', [])),
                'entities_count': len(supabase_data.get('entities', [])),
                'embeddings_count': len(supabase_data.get('embeddings', []))
            }
        else:
            raise Exception("数据保存失败")

    # ============================================================================
    # 便利方法
    # ============================================================================

    def process_multiple_files(self, srt_files: List[str]) -> List[Dict[str, Any]]:
        """批量处理多个字幕文件"""
        results = []

        for srt_file in srt_files:
            try:
                result = self.process_subtitle_file(srt_file)
                results.append(result)
            except Exception as e:
                logger.error(f"处理文件失败: {srt_file}, 错误: {e}")
                results.append({
                    'success': False,
                    'file': srt_file,
                    'error': str(e)
                })

        return results

    def get_project_summary(self) -> Dict[str, Any]:
        """获取项目摘要"""
        if not self.project_id:
            return {'error': 'No project ID'}

        project = self.db_client.get_project(self.project_id)
        if not project:
            return {'error': 'Project not found'}

        # 获取项目中的视频
        project_videos = self.db_client.client.table('project_videos').select(
            'video_id, videos(*)'
        ).eq('project_id', self.project_id).execute()

        videos_info = []
        total_atoms = 0
        total_duration = 0

        for pv in project_videos.data:
            video = pv['videos']
            if video:
                videos_info.append({
                    'id': video['id'],
                    'title': video['title'],
                    'status': video['status'],
                    'atom_count': video['atom_count'],
                    'duration_seconds': video['duration_seconds']
                })
                total_atoms += video['atom_count'] or 0
                total_duration += video['duration_seconds'] or 0

        return {
            'project': project,
            'videos_count': len(videos_info),
            'videos': videos_info,
            'total_atoms': total_atoms,
            'total_duration_minutes': total_duration // 60
        }


# ============================================================================
# 便利函数
# ============================================================================

def process_subtitle_with_supabase(srt_file_path: str, video_title: str = None,
                                  project_id: str = None) -> Dict[str, Any]:
    """
    便利函数：使用 Supabase 流水线处理字幕文件

    Args:
        srt_file_path: 字幕文件路径
        video_title: 视频标题
        project_id: 项目ID

    Returns:
        Dict: 处理结果
    """
    pipeline = SupabasePipeline(project_id)
    return pipeline.process_subtitle_file(srt_file_path, video_title=video_title)


if __name__ == "__main__":
    # 测试示例
    import sys

    if len(sys.argv) < 2:
        print("使用方法: python supabase_pipeline.py <字幕文件路径>")
        sys.exit(1)

    srt_file = sys.argv[1]
    result = process_subtitle_with_supabase(srt_file)
    print(f"处理结果: {result}")
