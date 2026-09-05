"""
KnowledgeGraphBuilder - 知识图谱构建器
构建实体-实体、实体-主题、实体-事件的知识图谱
"""

import json
from typing import List, Dict, Any, Set, Tuple
from pathlib import Path
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import NarrativeSegment
from utils import setup_logger

logger = setup_logger(__name__)


class KnowledgeGraphBuilder:
    """知识图谱构建器"""

    def build(
        self,
        segments: List[NarrativeSegment],
        entities: Dict[str, Any],
        topics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        构建知识图谱

        Args:
            segments: 叙事片段列表
            entities: 实体聚合数据
            topics: 主题网络数据

        Returns:
            知识图谱字典（nodes + edges）
        """
        logger.info(f"开始构建知识图谱")

        nodes = []
        edges = []
        node_ids = set()

        # 1. 添加实体节点
        for entity_type in ['persons', 'countries', 'organizations', 'events', 'concepts']:
            for entity in entities.get(entity_type, []):
                node_id = f"{entity_type}_{entity['name']}"
                if node_id not in node_ids:
                    nodes.append({
                        "id": node_id,
                        "type": entity_type,
                        "label": entity['name'],
                        "mentions": entity['mentions'],
                        "segments": entity['segments']
                    })
                    node_ids.add(node_id)

        # 2. 添加主题节点
        for topic in topics.get('primary_topics', []):
            node_id = f"topic_{topic['topic']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "type": "topic",
                    "label": topic['topic'],
                    "weight": topic['weight'],
                    "segments": topic['segments']
                })
                node_ids.add(node_id)

        # 3. 添加片段节点
        for segment in segments:
            node_id = f"segment_{segment.segment_id}"
            nodes.append({
                "id": node_id,
                "type": "segment",
                "label": segment.title,
                "duration_minutes": segment.duration_ms / 60000,
                "importance": segment.importance_score
            })
            node_ids.add(node_id)

        logger.info(f"  添加了 {len(nodes)} 个节点")

        # 4. 构建边（关系）

        # 4.1 实体 -> 片段关系
        for entity_type in ['persons', 'countries', 'organizations', 'events', 'concepts']:
            for entity in entities.get(entity_type, []):
                entity_id = f"{entity_type}_{entity['name']}"
                for segment_id in entity['segments']:
                    edges.append({
                        "source": entity_id,
                        "target": f"segment_{segment_id}",
                        "relation": "出现在",
                        "type": "entity_to_segment"
                    })

        # 4.2 主题 -> 片段关系
        for topic in topics.get('primary_topics', []):
            topic_id = f"topic_{topic['topic']}"
            for segment_id in topic['segments']:
                edges.append({
                    "source": topic_id,
                    "target": f"segment_{segment_id}",
                    "relation": "涵盖",
                    "type": "topic_to_segment"
                })

        # 4.3 实体 -> 主题关系
        for entity_type in ['persons', 'countries', 'organizations', 'events', 'concepts']:
            for entity in entities.get(entity_type, []):
                entity_id = f"{entity_type}_{entity['name']}"
                for context_topic in entity.get('context', []):
                    topic_id = f"topic_{context_topic}"
                    if topic_id in node_ids:
                        edges.append({
                            "source": entity_id,
                            "target": topic_id,
                            "relation": "相关主题",
                            "type": "entity_to_topic"
                        })

        # 4.4 实体共现关系（出现在同一片段的实体）
        segment_entities = defaultdict(lambda: defaultdict(list))
        for entity_type in ['persons', 'countries', 'organizations', 'events', 'concepts']:
            for entity in entities.get(entity_type, []):
                for segment_id in entity['segments']:
                    segment_entities[segment_id][entity_type].append(entity['name'])

        # 在同一片段中的实体建立关联
        for segment_id, entities_in_seg in segment_entities.items():
            # 人物与事件关联
            if entities_in_seg['persons'] and entities_in_seg['events']:
                for person in entities_in_seg['persons']:
                    for event in entities_in_seg['events']:
                        edges.append({
                            "source": f"persons_{person}",
                            "target": f"events_{event}",
                            "relation": "参与事件",
                            "type": "person_to_event"
                        })

            # 人物与国家关联
            if entities_in_seg['persons'] and entities_in_seg['countries']:
                for person in entities_in_seg['persons']:
                    for country in entities_in_seg['countries']:
                        edges.append({
                            "source": f"persons_{person}",
                            "target": f"countries_{country}",
                            "relation": "关联国家",
                            "type": "person_to_country"
                        })

            # 概念与事件关联
            if entities_in_seg['concepts'] and entities_in_seg['events']:
                for concept in entities_in_seg['concepts']:
                    for event in entities_in_seg['events']:
                        edges.append({
                            "source": f"concepts_{concept}",
                            "target": f"events_{event}",
                            "relation": "相关概念",
                            "type": "concept_to_event"
                        })

        # 去重边
        unique_edges = []
        edge_set = set()
        for edge in edges:
            edge_key = (edge['source'], edge['target'], edge['relation'])
            if edge_key not in edge_set:
                unique_edges.append(edge)
                edge_set.add(edge_key)

        logger.info(f"  添加了 {len(unique_edges)} 条边（去重后）")

        # 5. 构建图谱
        graph = {
            "nodes": nodes,
            "edges": unique_edges,
            "statistics": {
                "total_nodes": len(nodes),
                "total_edges": len(unique_edges),
                "node_types": self._count_node_types(nodes),
                "edge_types": self._count_edge_types(unique_edges)
            }
        }

        logger.info(f"知识图谱构建完成:")
        logger.info(f"  节点: {len(nodes)}个")
        logger.info(f"  边: {len(unique_edges)}条")

        return graph

    def _count_node_types(self, nodes: List[Dict]) -> Dict[str, int]:
        """统计节点类型"""
        counts = defaultdict(int)
        for node in nodes:
            counts[node['type']] += 1
        return dict(counts)

    def _count_edge_types(self, edges: List[Dict]) -> Dict[str, int]:
        """统计边类型"""
        counts = defaultdict(int)
        for edge in edges:
            counts[edge['type']] += 1
        return dict(counts)

    def save(self, graph: Dict[str, Any], output_path: Path):
        """保存知识图谱到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        logger.info(f"知识图谱已保存到: {output_path}")
