"""
实体索引数据模型
用于构建实体-原子映射关系，解决"点击实体看不到原子"的问题
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class EntityAtomMapping(BaseModel):
    """实体-原子映射关系"""
    entity_name: str = Field(description="实体名称")
    entity_type: str = Field(description="实体类型: person/country/organization/time_point/event/concept")
    mentions: int = Field(default=0, description="提及次数")

    # 原子详情列表
    atoms: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="关联原子详情: [{ atom_id, text_snippet, start_ms, end_ms, confidence }]"
    )

    # 段落关联
    segments: List[str] = Field(default_factory=list, description="所属时间段落 (SEG_001, SEG_002...)")
    narrative_segments: List[str] = Field(default_factory=list, description="所属叙事段落 (NARRATIVE_001...)")

    # 上下文信息
    context: List[str] = Field(default_factory=list, description="上下文描述")
    related_entities: List[str] = Field(default_factory=list, description="相关实体")


class EntityIndex(BaseModel):
    """实体索引数据结构"""
    entities: Dict[str, EntityAtomMapping] = Field(
        default_factory=dict,
        description="实体映射字典，key为实体名称"
    )

    # 元数据
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat(), description="最后更新时间")
    total_entities: int = Field(default=0, description="总实体数量")
    total_mappings: int = Field(default=0, description="总映射关系数量")

    # 统计信息
    statistics: Dict[str, Any] = Field(
        default_factory=dict,
        description="统计信息: by_type, by_segment等"
    )


class AtomAnnotation(BaseModel):
    """原子标注信息"""
    atom_id: str

    # 语义标注
    entities: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="包含的实体: [{ name, type, confidence }]"
    )
    topics: List[str] = Field(default_factory=list, description="关联主题")

    # 情感分析
    emotion: Optional[Dict[str, Any]] = Field(None, description="情感标注: { type, score, confidence }")

    # 重要性评分
    importance_score: float = Field(default=0.5, description="重要性评分 0-1")

    # 状态标记
    has_entity: bool = Field(default=False, description="是否包含实体")
    has_topic: bool = Field(default=False, description="是否包含主题")
    embedding_status: str = Field(default="pending", description="向量化状态: pending/completed/failed")

    # 关联信息
    parent_segment_id: Optional[str] = Field(None, description="所属时间段落ID")
    parent_narrative_id: Optional[str] = Field(None, description="所属叙事段落ID")


class EntitySearchResult(BaseModel):
    """实体搜索结果"""
    entity_name: str
    entity_type: str
    total_mentions: int

    # 匹配的原子
    matching_atoms: List[Dict[str, Any]] = Field(
        description="匹配的原子: [{ atom_id, text_snippet, start_ms, confidence, segment_id }]"
    )

    # 时间分布
    time_distribution: List[Dict[str, Any]] = Field(
        description="时间分布: [{ segment_id, mentions, start_time, end_time }]"
    )


def build_entity_atom_precise_mapping(
    entities: Dict[str, Any],
    atoms: List[Any]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    构建精确的实体-原子映射

    Args:
        entities: 实体数据 (来自entities.json)
        atoms: 原子列表 (来自atoms.jsonl)

    Returns:
        Dict[str, List[Dict]]: {
            "entity_name": [
                {
                    "atom_id": "A001",
                    "text_snippet": "...习近平主席...",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "confidence": 0.95
                }
            ]
        }
    """

    mapping = {}
    atoms_dict = {atom.atom_id if hasattr(atom, 'atom_id') else atom['atom_id']: atom for atom in atoms}

    for entity_type in ['persons', 'countries', 'organizations',
                       'time_points', 'events', 'concepts']:
        for entity in entities.get(entity_type, []):
            entity_name = entity['name']
            atom_details = []

            # 从现有的atoms字段获取（如果存在）
            entity_atom_ids = entity.get('atoms', [])

            # 如果没有atoms字段，尝试通过文本匹配查找
            if not entity_atom_ids:
                entity_atom_ids = find_entity_atoms_by_text(entity_name, atoms)

            for atom_id in entity_atom_ids:
                if atom_id not in atoms_dict:
                    continue

                atom = atoms_dict[atom_id]
                atom_text = atom.merged_text if hasattr(atom, 'merged_text') else atom['merged_text']
                atom_start = atom.start_ms if hasattr(atom, 'start_ms') else atom['start_ms']
                atom_end = atom.end_ms if hasattr(atom, 'end_ms') else atom['end_ms']

                # 提取包含实体的文本片段（上下文）
                snippet = extract_context_snippet(atom_text, entity_name, context_chars=50)

                # 计算置信度
                confidence = calculate_match_confidence(entity_name, atom_text)

                atom_details.append({
                    "atom_id": atom_id,
                    "text_snippet": snippet,
                    "start_ms": atom_start,
                    "end_ms": atom_end,
                    "confidence": confidence,
                    "full_text": atom_text
                })

            if atom_details:  # 只保存有原子关联的实体
                mapping[entity_name] = atom_details

    return mapping


def find_entity_atoms_by_text(entity_name: str, atoms: List[Any]) -> List[str]:
    """通过文本匹配查找包含实体的原子"""
    matching_atom_ids = []

    for atom in atoms:
        atom_id = atom.atom_id if hasattr(atom, 'atom_id') else atom['atom_id']
        atom_text = atom.merged_text if hasattr(atom, 'merged_text') else atom['merged_text']

        # 精确匹配
        if entity_name in atom_text:
            matching_atom_ids.append(atom_id)
            continue

        # 模糊匹配（处理变体）
        if fuzzy_match(entity_name, atom_text):
            matching_atom_ids.append(atom_id)

    return matching_atom_ids


def extract_context_snippet(text: str, entity: str, context_chars: int = 50) -> str:
    """提取实体周围的文本片段"""
    idx = text.find(entity)
    if idx == -1:
        return text[:100] + "..." if len(text) > 100 else text

    start = max(0, idx - context_chars)
    end = min(len(text), idx + len(entity) + context_chars)

    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet


def calculate_match_confidence(entity_name: str, text: str) -> float:
    """计算匹配置信度"""
    if entity_name in text:
        # 精确匹配 - 基于上下文相关性调整置信度
        context_length = len(text)
        entity_length = len(entity_name)

        # 基础置信度
        base_confidence = 1.0

        # 如果实体占文本比例很小，置信度略降
        if context_length > 0:
            ratio = entity_length / context_length
            if ratio < 0.1:  # 实体占比小于10%
                base_confidence = 0.9

        return base_confidence

    # 模糊匹配（可使用 difflib 或其他算法）
    try:
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, entity_name, text).ratio()
        # 只有相似度很高才认为是匹配
        return ratio if ratio > 0.8 else 0.0
    except:
        return 0.0


def fuzzy_match(entity_name: str, text: str, threshold: float = 0.8) -> bool:
    """模糊匹配实体名称"""
    try:
        from difflib import SequenceMatcher

        # 方法1: 整体相似度匹配
        ratio = SequenceMatcher(None, entity_name, text).ratio()
        if ratio > threshold:
            return True

        # 方法2: 子字符串匹配（适用于中文姓名变体）
        if len(entity_name) >= 2:
            # 检查实体名称的主要部分是否在文本中
            main_part = entity_name[:2] if len(entity_name) > 2 else entity_name
            if main_part in text:
                return True

        # 方法3: 处理特殊缩写（如 "中国人民银行" vs "央行"）
        # TODO: 可以添加更多规则

        return False
    except:
        return False