# -*- coding: utf-8 -*-
"""
叙事片段生成服务
Narrative Segment Generation Service

基于已有原子数据生成叙事片段（智能切分+深度分析）
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import List, Dict, Any, Generator
import json
from datetime import datetime
import logging

from models import Atom, SegmentMeta, NarrativeSegment
from structurers import SegmentIdentifier
from analyzers import DeepAnalyzer
from analyzers.entity_extractor import EntityExtractor
from analyzers.topic_network_builder import TopicNetworkBuilder
from analyzers.knowledge_graph_builder import KnowledgeGraphBuilder
from utils import save_json, load_jsonl

logger = logging.getLogger(__name__)


class NarrativeGenerator:
    """叙事片段生成器"""

    def __init__(self, data_dir: Path, api_key: str):
        self.data_dir = Path(data_dir)
        self.api_key = api_key

    def generate_segments_stream(self) -> Generator[Dict[str, Any], None, None]:
        """
        生成叙事片段（流式返回进度）

        Yields:
            进度消息字典
        """
        try:
            # Step 1: 加载原子数据和标注
            yield {
                "type": "progress",
                "stage": "loading",
                "message": "正在加载原子数据和分析结果...",
                "progress": 0
            }

            atoms = self._load_atoms()
            atom_annotations = self._load_atom_annotations()
            yield {
                "type": "progress",
                "stage": "loading",
                "message": f"已加载 {len(atoms)} 个原子",
                "progress": 10,
                "data": {"atom_count": len(atoms)}
            }

            # Step 2: 识别叙事片段（智能切分）
            yield {
                "type": "progress",
                "stage": "identifying",
                "message": "正在识别叙事片段边界...",
                "progress": 20
            }

            identifier = SegmentIdentifier(self.api_key)
            segment_metas = identifier.identify_segments(atoms)

            yield {
                "type": "progress",
                "stage": "identifying",
                "message": f"已识别 {len(segment_metas)} 个叙事片段",
                "progress": 40,
                "data": {"segment_count": len(segment_metas)}
            }

            # Step 3: 深度分析每个片段
            yield {
                "type": "progress",
                "stage": "analyzing",
                "message": "正在深度分析叙事片段...",
                "progress": 50
            }

            analyzer = DeepAnalyzer(self.api_key)
            narrative_segments = []

            for i, seg_meta in enumerate(segment_metas):
                # 分析单个片段
                seg_atoms = [a for a in atoms if a.atom_id in seg_meta.atoms]
                narrative_seg = analyzer.analyze_segment(seg_meta, seg_atoms, atom_annotations)
                narrative_segments.append(narrative_seg)

                # 报告进度
                progress = 50 + int((i + 1) / len(segment_metas) * 30)
                yield {
                    "type": "progress",
                    "stage": "analyzing",
                    "message": f"已分析 {i+1}/{len(segment_metas)} 个片段: {narrative_seg.title}",
                    "progress": progress,
                    "data": {
                        "current_segment": i + 1,
                        "total_segments": len(segment_metas),
                        "segment_id": narrative_seg.segment_id,
                        "segment_title": narrative_seg.title
                    }
                }

            yield {
                "type": "progress",
                "stage": "analyzing",
                "message": f"深度分析完成，共 {len(narrative_segments)} 个片段",
                "progress": 80
            }

            # Step 4: 构建知识索引
            yield {
                "type": "progress",
                "stage": "indexing",
                "message": "正在构建知识索引（实体、主题、图谱）...",
                "progress": 85
            }

            entities_data, topics_data, graph_data = self._build_knowledge_indexes(
                narrative_segments, atoms
            )

            # Step 5: 保存结果
            yield {
                "type": "progress",
                "stage": "saving",
                "message": "正在保存结果...",
                "progress": 90
            }

            self._save_results(narrative_segments, entities_data, topics_data, graph_data)

            # 完成
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "叙事片段生成完成！",
                "progress": 100,
                "data": {
                    "atom_count": len(atoms),
                    "segment_count": len(narrative_segments),
                    "segments": [
                        {
                            "segment_id": seg.segment_id,
                            "title": seg.title,
                            "duration_minutes": seg.duration_minutes,
                            "atom_count": len(seg.atoms)
                        }
                        for seg in narrative_segments
                    ]
                }
            }

        except Exception as e:
            logger.error(f"生成叙事片段失败: {e}", exc_info=True)
            yield {
                "type": "error",
                "stage": "error",
                "message": f"生成失败: {str(e)}",
                "progress": -1
            }

    def _load_atoms(self) -> List[Atom]:
        """加载原子数据"""
        atoms_file = self.data_dir / "atoms.jsonl"
        if not atoms_file.exists():
            raise FileNotFoundError(f"原子文件不存在: {atoms_file}")

        atoms_data = load_jsonl(str(atoms_file))
        atoms = [Atom(**data) for data in atoms_data]
        return atoms

    def _load_atom_annotations(self) -> Dict[str, Any]:
        """
        加载原子的富化分析数据

        Returns:
            {atom_id: annotation_data}
        """
        annotations_file = self.data_dir / "atom_annotations.json"
        if not annotations_file.exists():
            logger.warning(f"原子标注文件不存在: {annotations_file}")
            return {}

        try:
            annotations_list = load_json(str(annotations_file))
            # 转换为字典格式方便查询
            annotations_dict = {ann['atom_id']: ann for ann in annotations_list}
            logger.info(f"已加载 {len(annotations_dict)} 个原子的分析数据")
            return annotations_dict
        except Exception as e:
            logger.warning(f"加载原子标注失败: {e}")
            return {}

    def _build_knowledge_indexes(
        self,
        narrative_segments: List[NarrativeSegment],
        atoms: List[Atom]
    ) -> tuple:
        """构建知识索引"""
        try:
            # 提取实体
            entity_extractor = EntityExtractor(self.api_key)
            entities_data = entity_extractor.extract_from_segments(narrative_segments)

            # 构建主题网络
            topic_builder = TopicNetworkBuilder(self.api_key)
            topics_data = topic_builder.build_from_segments(narrative_segments)

            # 构建知识图谱
            graph_builder = KnowledgeGraphBuilder()
            graph_data = graph_builder.build(narrative_segments, entities_data)

            return entities_data, topics_data, graph_data

        except Exception as e:
            logger.warning(f"知识索引构建失败: {e}")
            return {}, {}, {}

    def _save_results(
        self,
        narrative_segments: List[NarrativeSegment],
        entities_data: Dict,
        topics_data: Dict,
        graph_data: Dict
    ):
        """保存结果"""
        # 保存叙事片段
        segments_file = self.data_dir / "narrative_segments.json"
        segments_dict = [seg.dict() for seg in narrative_segments]
        save_json(segments_dict, str(segments_file))
        logger.info(f"已保存叙事片段: {segments_file}")

        # 保存实体索引
        if entities_data:
            entities_file = self.data_dir / "entities.json"
            save_json(entities_data, str(entities_file))
            logger.info(f"已保存实体索引: {entities_file}")

        # 保存主题网络
        if topics_data:
            topics_file = self.data_dir / "topics.json"
            save_json(topics_data, str(topics_file))
            logger.info(f"已保存主题网络: {topics_file}")

        # 保存知识图谱
        if graph_data:
            graph_file = self.data_dir / "knowledge_graph.json"
            save_json(graph_data, str(graph_file))
            logger.info(f"已保存知识图谱: {graph_file}")

        logger.info("所有结果已保存")
