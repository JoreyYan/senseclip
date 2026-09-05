"""
EntityExtractor - 实体提取聚合器
从所有叙事片段中提取和聚合实体信息
"""

import json
import re
from typing import List, Dict, Any, Set
from pathlib import Path
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import NarrativeSegment
from models.entity_index import build_entity_atom_precise_mapping, extract_context_snippet, calculate_match_confidence
from utils import setup_logger

logger = setup_logger(__name__)


class EntityExtractor:
    """实体提取聚合器"""

    def __init__(self):
        """初始化实体提取器，包含别名映射"""
        self.entity_aliases = {
            # 人物别名映射 - 将所有变体映射到标准名称
            "persons": {
                "罗兴汉": "罗星汉",  # 标准化名称
                "罗兴汉投降": "罗星汉",
                "罗兴汉越狱": "罗星汉",
                "罗兴汉经商转型": "罗星汉",
                "张奇夫": "坤沙",
                "张奇夫(昆沙)": "坤沙",
                "坤沙(张奇夫)": "坤沙",
                "昆沙": "坤沙",
                "毛泽东": "毛主席",  # 毛泽东在文本中主要以毛主席形式出现
                "毛主席": "毛主席"   # 保持一致
            },
            # 可以添加其他类型的别名
            "organizations": {},
            "countries": {},
            "events": {},
            "concepts": {}
        }

    def _normalize_entity_name(self, entity_name: str, entity_type: str) -> str:
        """标准化实体名称，处理别名"""
        if entity_type in self.entity_aliases:
            return self.entity_aliases[entity_type].get(entity_name, entity_name)
        return entity_name

    def _enhanced_entity_match(self, entity_name: str, text: str) -> bool:
        """
        改进的实体匹配算法
        处理中文字符变体、别名映射和模糊匹配
        """
        if not entity_name or not text:
            return False

        # 首先检查是否有别名映射需要考虑
        search_terms = [entity_name]

        # 如果实体名是"毛主席"，也搜索"毛泽东"
        if entity_name == "毛主席":
            search_terms.append("毛泽东")
        elif entity_name == "毛泽东":
            search_terms.append("毛主席")

        # 直接匹配所有搜索词
        for term in search_terms:
            if term in text:
                return True

        # 处理常见中文字符变体
        variants = {
            "星": ["兴", "星"],
            "兴": ["兴", "星"],
            "昆": ["昆", "坤"],
            "坤": ["坤", "昆"]
        }

        # 生成所有搜索词的字符变体
        possible_names = list(search_terms)

        for search_term in search_terms:
            for char, replacements in variants.items():
                if char in search_term:
                    for replacement in replacements:
                        if replacement != char:
                            variant_name = search_term.replace(char, replacement)
                            if variant_name not in possible_names:
                                possible_names.append(variant_name)

        # 检查所有变体是否在文本中
        for name_variant in possible_names:
            if name_variant in text:
                return True

        return False

    def _extract_entity_from_compound(self, compound_entity: str) -> str:
        """
        从复合实体名中提取核心实体名
        例如：'罗兴汉投降' -> '罗兴汉'
        """
        # 常见的动作词汇，需要从实体名中移除
        action_words = ["投降", "越狱", "经商转型", "起义", "战死", "投靠", "叛变"]

        for action in action_words:
            if compound_entity.endswith(action):
                return compound_entity[:-len(action)]

        return compound_entity

    def extract(self, segments: List[NarrativeSegment], atoms: List = None) -> Dict[str, Any]:
        """
        从所有片段中提取并聚合实体

        Args:
            segments: 叙事片段列表
            atoms: 原子列表（用于精确定位实体所在的原子）

        Returns:
            实体聚合结果字典
        """
        logger.info(f"开始提取实体，共{len(segments)}个片段")

        # 初始化实体字典
        entities_agg = {
            "persons": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            }),
            "countries": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            }),
            "organizations": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            }),
            "time_points": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            }),
            "events": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            }),
            "concepts": defaultdict(lambda: {
                "name": "",
                "mentions": 0,
                "segments": [],
                "atoms": set(),
                "context": set()
            })
        }

        # 构建atom_id -> text映射（用于查找实体）
        atom_texts = {}
        if atoms:
            for atom in atoms:
                # Handle both Atom objects and dict objects
                if hasattr(atom, 'atom_id'):
                    atom_id = atom.atom_id
                    merged_text = atom.merged_text
                else:
                    # Dict format from segment_manager.load_atoms()
                    atom_id = atom['atom_id']
                    merged_text = atom['merged_text']
                atom_texts[atom_id] = merged_text

        # 遍历所有片段
        for segment in segments:
            segment_id = segment.segment_id
            entities = segment.entities

            # 对每个实体类型进行处理
            entity_types = [
                ("persons", entities.persons),
                ("countries", entities.countries),
                ("organizations", entities.organizations),
                ("time_points", entities.time_points),
                ("events", entities.events),
                ("concepts", entities.concepts)
            ]

            for entity_type, entity_list in entity_types:
                for raw_entity_name in entity_list:
                    # 标准化实体名称，处理别名和复合实体
                    core_entity = self._extract_entity_from_compound(raw_entity_name)
                    normalized_name = self._normalize_entity_name(core_entity, entity_type)

                    # 使用标准化后的名称作为key
                    entities_agg[entity_type][normalized_name]["name"] = normalized_name
                    entities_agg[entity_type][normalized_name]["mentions"] += 1
                    entities_agg[entity_type][normalized_name]["segments"].append(segment_id)

                    # 使用改进的算法找到包含该实体的具体原子
                    if atom_texts and atoms:
                        for atom_ref in segment.atoms:
                            atom_id = None
                            atom_text = None

                            # Handle both numeric indices and string IDs
                            if isinstance(atom_ref, int):
                                # Numeric index - map to actual atom
                                if 0 <= atom_ref < len(atoms):
                                    atom = atoms[atom_ref]
                                    if hasattr(atom, 'atom_id'):
                                        atom_id = atom.atom_id
                                    else:
                                        atom_id = atom['atom_id']
                                    atom_text = atom_texts.get(atom_id)
                            else:
                                # String ID - direct lookup
                                atom_id = atom_ref
                                atom_text = atom_texts.get(atom_id)

                            if atom_id and atom_text:
                                # 使用增强的实体匹配算法
                                if self._enhanced_entity_match(normalized_name, atom_text):
                                    entities_agg[entity_type][normalized_name]["atoms"].add(atom_id)

                    # 添加上下文（从片段主题）
                    if segment.topics.primary_topic:
                        entities_agg[entity_type][normalized_name]["context"].add(segment.topics.primary_topic)

        # 重新计算基于原子级别的accurate mentions
        if atom_texts:
            result = self._recalculate_mentions_from_atoms(entities_agg, atom_texts)
        else:
            result = self._format_entities(entities_agg)

        logger.info(f"实体提取完成:")
        logger.info(f"  人物: {len(result['persons'])}个")
        logger.info(f"  国家: {len(result['countries'])}个")
        logger.info(f"  组织: {len(result['organizations'])}个")
        logger.info(f"  时间点: {len(result['time_points'])}个")
        logger.info(f"  事件: {len(result['events'])}个")
        logger.info(f"  概念: {len(result['concepts'])}个")

        return result

    def _recalculate_mentions_from_atoms(self, entities_agg: Dict, atom_texts: Dict) -> Dict[str, Any]:
        """
        基于原子级别重新计算accurate mentions数和原子映射
        这确保了mentions计数与实际在原子中的出现次数一致，并正确映射所有包含该实体的原子
        """
        for entity_type, entities in entities_agg.items():
            for entity_name, entity_data in entities.items():
                # 重置mentions计数和atoms集合
                actual_mentions = 0
                entity_data["atoms"].clear()  # 清空原有的错误映射

                # 遍历所有原子，计算实际出现次数并收集包含该实体的原子ID
                for atom_id, atom_text in atom_texts.items():
                    if self._enhanced_entity_match(entity_name, atom_text):
                        # 计算在该原子中的出现次数，考虑所有变体
                        count_in_atom = 0

                        # 搜索实体名本身
                        count_in_atom += atom_text.count(entity_name)

                        # 检查毛泽东/毛主席别名
                        if entity_name == "毛主席":
                            count_in_atom += atom_text.count("毛泽东")
                        elif entity_name == "毛泽东":
                            count_in_atom += atom_text.count("毛主席")

                        # 检查字符变体
                        if "星" in entity_name:
                            variant = entity_name.replace("星", "兴")
                            count_in_atom += atom_text.count(variant)
                        elif "兴" in entity_name:
                            variant = entity_name.replace("兴", "星")
                            count_in_atom += atom_text.count(variant)

                        # 如果该原子包含实体，添加到atoms集合
                        if count_in_atom > 0:
                            entity_data["atoms"].add(atom_id)
                            actual_mentions += count_in_atom

                # 更新为准确的mentions数
                entity_data["mentions"] = max(actual_mentions, entity_data["mentions"])

        return self._format_entities(entities_agg)

    def _format_entities(self, entities_agg: Dict) -> Dict[str, Any]:
        """格式化实体数据"""
        result = {}

        for entity_type, entities in entities_agg.items():
            formatted_entities = []
            for entity_data in entities.values():
                # 对atoms进行排序（按atom_id）
                atoms_list = sorted(list(entity_data["atoms"]))

                formatted_entities.append({
                    "name": entity_data["name"],
                    "mentions": entity_data["mentions"],
                    "segments": list(set(entity_data["segments"])),  # 去重
                    "atoms": atoms_list,  # 已排序的原子列表
                    "context": list(entity_data["context"]),  # 转为列表
                    "related_entities": []  # 后续可扩展
                })

            # 按提及次数排序
            formatted_entities.sort(key=lambda x: x["mentions"], reverse=True)
            result[entity_type] = formatted_entities

        # 添加统计信息
        result["statistics"] = {
            "total_entities": sum(len(entities) for entities in result.values() if isinstance(entities, list)),
            "by_type": {
                entity_type: len(entities)
                for entity_type, entities in result.items()
                if isinstance(entities, list)
            }
        }

        return result

    def extract_with_precise_matching(self, segments: List[NarrativeSegment], atoms: List) -> Dict[str, Any]:
        """
        使用精确匹配从片段中提取实体并建立准确的实体-原子映射关系

        Args:
            segments: 叙事片段列表
            atoms: 原子列表

        Returns:
            增强的实体数据，包含精确的原子映射
        """
        logger.info(f"开始精确匹配实体提取，共{len(segments)}个片段, {len(atoms)}个原子")

        # 首先使用标准方法提取实体
        entities_data = self.extract(segments, atoms)

        # 使用精确匹配功能增强实体-原子映射
        enhanced_mapping = build_entity_atom_precise_mapping(entities_data, atoms)

        # 更新实体数据，添加精确的原子映射信息
        for entity_type, entity_list in entities_data.items():
            if isinstance(entity_list, list):  # 跳过statistics
                for entity in entity_list:
                    entity_name = entity.get('name')
                    if entity_name in enhanced_mapping:
                        # 更新原子信息，包含上下文片段和置信度
                        entity['atoms_detailed'] = enhanced_mapping[entity_name]
                        entity['total_atom_matches'] = len(enhanced_mapping[entity_name])

                        # 计算平均置信度
                        if enhanced_mapping[entity_name]:
                            avg_confidence = sum(
                                atom_detail.get('confidence', 0.0)
                                for atom_detail in enhanced_mapping[entity_name]
                            ) / len(enhanced_mapping[entity_name])
                            entity['avg_confidence'] = round(avg_confidence, 3)
                        else:
                            entity['avg_confidence'] = 0.0

        logger.info(f"精确匹配完成，增强了 {len(enhanced_mapping)} 个实体的原子映射")
        return entities_data

    def build_entity_index_for_segment(self, segment_entities: Dict[str, Any], segment_atoms: List, segment_id: str) -> Dict[str, Any]:
        """
        为特定段落构建实体索引

        Args:
            segment_entities: 段落实体数据
            segment_atoms: 段落原子列表
            segment_id: 段落ID

        Returns:
            段落实体索引数据
        """
        logger.info(f"为段落 {segment_id} 构建实体索引")

        # 构建精确映射
        mapping = build_entity_atom_precise_mapping(segment_entities, segment_atoms)

        # 格式化为索引结构
        entity_index = {}

        for entity_name, atom_details in mapping.items():
            if atom_details:  # 只包含有原子关联的实体
                entity_index[entity_name] = {
                    "entity_name": entity_name,
                    "entity_type": self._determine_entity_type(entity_name, segment_entities),
                    "mentions": len(atom_details),
                    "atoms": atom_details,
                    "segments": [segment_id],
                    "narrative_segments": [],  # 可后续填充
                    "context": [atom['full_text'][:50] + "..." for atom in atom_details[:3]],  # 前3个原子的上下文
                    "related_entities": []  # 可后续分析填充
                }

        logger.info(f"段落 {segment_id} 实体索引构建完成，包含 {len(entity_index)} 个实体")
        return entity_index

    def _determine_entity_type(self, entity_name: str, entities_data: Dict[str, Any]) -> str:
        """确定实体类型"""
        for entity_type, entity_list in entities_data.items():
            if isinstance(entity_list, list):
                for entity in entity_list:
                    if entity.get('name') == entity_name:
                        return entity_type
        return "unknown"

    def save(self, entities: Dict[str, Any], output_path: Path):
        """保存实体数据到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(entities, f, ensure_ascii=False, indent=2)
        logger.info(f"实体数据已保存到: {output_path}")
