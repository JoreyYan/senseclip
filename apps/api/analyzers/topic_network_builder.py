"""
TopicNetworkBuilder - 主题网络构建器
从叙事片段中构建主题网络和关系图
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


class TopicNetworkBuilder:
    """主题网络构建器"""

    def build(self, segments: List[NarrativeSegment]) -> Dict[str, Any]:
        """
        构建主题网络

        Args:
            segments: 叙事片段列表

        Returns:
            主题网络字典
        """
        logger.info(f"开始构建主题网络，共{len(segments)}个片段")

        # 收集所有主题
        primary_topics = defaultdict(lambda: {
            "topic": "",
            "weight": 0.0,
            "segments": [],
            "atoms": [],
            "subtopics": set(),
            "tags": set()
        })

        secondary_topics = defaultdict(lambda: {
            "topic": "",
            "weight": 0.0,
            "segments": [],
            "atoms": [],
            "parent_topics": set()
        })

        all_tags = defaultdict(lambda: {
            "tag": "",
            "count": 0,
            "segments": [],
            "related_topics": set()
        })

        # 遍历片段提取主题
        for segment in segments:
            segment_id = segment.segment_id
            topics = segment.topics

            # 主要主题
            if topics.primary_topic:
                primary_topics[topics.primary_topic]["topic"] = topics.primary_topic
                primary_topics[topics.primary_topic]["weight"] += segment.importance_score
                primary_topics[topics.primary_topic]["segments"].append(segment_id)
                primary_topics[topics.primary_topic]["atoms"].extend(segment.atoms)

                # 关联次要主题
                for sec_topic in topics.secondary_topics:
                    primary_topics[topics.primary_topic]["subtopics"].add(sec_topic)

                # 关联标签
                for tag in topics.free_tags:
                    primary_topics[topics.primary_topic]["tags"].add(tag)

            # 次要主题
            for sec_topic in topics.secondary_topics:
                secondary_topics[sec_topic]["topic"] = sec_topic
                secondary_topics[sec_topic]["weight"] += segment.importance_score * 0.6
                secondary_topics[sec_topic]["segments"].append(segment_id)
                secondary_topics[sec_topic]["atoms"].extend(segment.atoms)
                if topics.primary_topic:
                    secondary_topics[sec_topic]["parent_topics"].add(topics.primary_topic)

            # 标签
            for tag in topics.free_tags:
                all_tags[tag]["tag"] = tag
                all_tags[tag]["count"] += 1
                all_tags[tag]["segments"].append(segment_id)
                if topics.primary_topic:
                    all_tags[tag]["related_topics"].add(topics.primary_topic)

        # 构建主题关系
        topic_relations = self._build_topic_relations(primary_topics, secondary_topics)

        # 格式化结果
        result = {
            "primary_topics": self._format_topics(primary_topics),
            "secondary_topics": self._format_secondary_topics(secondary_topics),
            "tags": self._format_tags(all_tags),
            "topic_relations": topic_relations,
            "statistics": {
                "total_primary_topics": len(primary_topics),
                "total_secondary_topics": len(secondary_topics),
                "total_tags": len(all_tags),
                "total_relations": len(topic_relations)
            }
        }

        logger.info(f"主题网络构建完成:")
        logger.info(f"  主要主题: {len(primary_topics)}个")
        logger.info(f"  次要主题: {len(secondary_topics)}个")
        logger.info(f"  标签: {len(all_tags)}个")
        logger.info(f"  关系: {len(topic_relations)}条")

        return result

    def _build_topic_relations(
        self,
        primary_topics: Dict,
        secondary_topics: Dict
    ) -> List[Dict[str, Any]]:
        """构建主题之间的关系"""
        relations = []

        # 主题 -> 次要主题关系
        for primary_topic, data in primary_topics.items():
            for subtopic in data["subtopics"]:
                relations.append({
                    "from": primary_topic,
                    "to": subtopic,
                    "relation": "包含",
                    "strength": 0.8
                })

        # 次要主题 -> 主要主题关系
        for sec_topic, data in secondary_topics.items():
            for parent_topic in data["parent_topics"]:
                # 避免重复（已经在上面添加了）
                pass

        # 共现关系：出现在同一片段的主题
        topic_cooccurrence = defaultdict(set)
        for primary_topic, data in primary_topics.items():
            for segment_id in data["segments"]:
                topic_cooccurrence[segment_id].add(primary_topic)

        # 查找共现主题对
        cooccurrence_pairs = set()
        for segment_id, topics_in_seg in topic_cooccurrence.items():
            topics_list = list(topics_in_seg)
            for i in range(len(topics_list)):
                for j in range(i+1, len(topics_list)):
                    pair = tuple(sorted([topics_list[i], topics_list[j]]))
                    cooccurrence_pairs.add(pair)

        for topic1, topic2 in cooccurrence_pairs:
            relations.append({
                "from": topic1,
                "to": topic2,
                "relation": "共现",
                "strength": 0.5
            })

        return relations

    def _format_topics(self, topics: Dict) -> List[Dict[str, Any]]:
        """格式化主要主题"""
        formatted = []
        for topic_data in topics.values():
            formatted.append({
                "topic": topic_data["topic"],
                "weight": round(topic_data["weight"], 2),
                "segments": list(set(topic_data["segments"])),
                "atoms": list(set(topic_data["atoms"])),
                "subtopics": list(topic_data["subtopics"]),
                "tags": list(topic_data["tags"])[:10]  # 限制标签数量
            })
        # 按权重排序
        formatted.sort(key=lambda x: x["weight"], reverse=True)
        return formatted

    def _format_secondary_topics(self, topics: Dict) -> List[Dict[str, Any]]:
        """格式化次要主题"""
        formatted = []
        for topic_data in topics.values():
            formatted.append({
                "topic": topic_data["topic"],
                "weight": round(topic_data["weight"], 2),
                "segments": list(set(topic_data["segments"])),
                "atoms": list(set(topic_data["atoms"])),
                "parent_topics": list(topic_data["parent_topics"])
            })
        formatted.sort(key=lambda x: x["weight"], reverse=True)
        return formatted

    def _format_tags(self, tags: Dict) -> List[Dict[str, Any]]:
        """格式化标签"""
        formatted = []
        for tag_data in tags.values():
            formatted.append({
                "tag": tag_data["tag"],
                "count": tag_data["count"],
                "segments": list(set(tag_data["segments"])),
                "related_topics": list(tag_data["related_topics"])
            })
        formatted.sort(key=lambda x: x["count"], reverse=True)
        return formatted

    def save(self, topic_network: Dict[str, Any], output_path: Path):
        """保存主题网络到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(topic_network, f, ensure_ascii=False, indent=2)
        logger.info(f"主题网络已保存到: {output_path}")
