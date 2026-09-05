"""
AtomAnnotator - 原子级别语义标注器
为单个原子提供实体、主题、情感等多维度语义标注
"""

import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.entity_index import AtomAnnotation
from analyzers.deep_analyzer import DeepAnalyzer
from utils import setup_logger

logger = setup_logger(__name__)


class AtomAnnotator:
    """原子级别语义标注器"""

    def __init__(self, api_key: str = None):
        """
        初始化标注器

        Args:
            api_key: Claude API密钥，用于深度分析
        """
        self.api_key = api_key
        from config import DEEPSEEK_API_KEY
        self.deep_analyzer = DeepAnalyzer(api_key, deepseek_api_key=DEEPSEEK_API_KEY) if api_key else None
        logger.info("AtomAnnotator 初始化完成")

    def annotate_atom(self, atom: Any, segment_id: str = None, narrative_id: str = None) -> AtomAnnotation:
        """
        为单个原子进行完整的语义标注

        Args:
            atom: 原子对象
            segment_id: 所属时间段落ID
            narrative_id: 所属叙事段落ID

        Returns:
            AtomAnnotation: 完整的标注信息
        """
        atom_id = getattr(atom, 'atom_id', atom.get('atom_id') if isinstance(atom, dict) else '')
        atom_text = getattr(atom, 'merged_text', atom.get('merged_text') if isinstance(atom, dict) else '')

        logger.debug(f"开始标注原子 {atom_id}")

        # 基础标注信息
        annotation = AtomAnnotation(
            atom_id=atom_id,
            parent_segment_id=segment_id,
            parent_narrative_id=narrative_id
        )

        # 1. 实体提取和标注
        entities = self._extract_entities_from_text(atom_text)
        if entities:
            annotation.entities = entities
            annotation.has_entity = True

        # 2. 主题提取
        topics = self._extract_topics_from_text(atom_text)
        if topics:
            annotation.topics = topics
            annotation.has_topic = True

        # 3. 情感分析
        emotion = self._analyze_emotion(atom_text)
        if emotion:
            annotation.emotion = emotion

        # 4. 重要性评分
        annotation.importance_score = self._calculate_importance_score(
            atom_text, entities, topics, emotion
        )

        # 5. 设置初始嵌入状态
        annotation.embedding_status = "pending"

        logger.debug(f"原子 {atom_id} 标注完成: {len(entities or [])} 个实体, {len(topics or [])} 个主题")
        return annotation

    def annotate_atoms_batch(
        self,
        atoms: List[Any],
        segment_id: str = None,
        narrative_id: str = None,
        batch_size: int = 10
    ) -> List[AtomAnnotation]:
        """
        批量标注原子

        Args:
            atoms: 原子列表
            segment_id: 所属时间段落ID
            narrative_id: 所属叙事段落ID
            batch_size: 批处理大小

        Returns:
            List[AtomAnnotation]: 标注结果列表
        """
        logger.info(f"开始批量标注 {len(atoms)} 个原子")
        annotations = []

        # 分批处理
        for i in range(0, len(atoms), batch_size):
            batch = atoms[i:i + batch_size]
            batch_annotations = []

            for atom in batch:
                try:
                    annotation = self.annotate_atom(atom, segment_id, narrative_id)
                    batch_annotations.append(annotation)
                except Exception as e:
                    logger.error(f"标注原子失败: {e}")
                    # 创建基础标注
                    atom_id = getattr(atom, 'atom_id', atom.get('atom_id') if isinstance(atom, dict) else f'unknown_{i}')
                    basic_annotation = AtomAnnotation(
                        atom_id=atom_id,
                        parent_segment_id=segment_id,
                        parent_narrative_id=narrative_id,
                        embedding_status="failed"
                    )
                    batch_annotations.append(basic_annotation)

            annotations.extend(batch_annotations)
            logger.info(f"已完成批次 {i//batch_size + 1}/{(len(atoms) + batch_size - 1)//batch_size}")

        logger.info(f"批量标注完成，共标注 {len(annotations)} 个原子")
        return annotations

    def _extract_entities_from_text(self, text: str) -> List[Dict[str, Any]]:
        """从文本中提取实体"""
        if not text or not text.strip():
            return []

        # 如果有深度分析器，使用AI提取
        if self.deep_analyzer:
            try:
                # 使用深度分析器的实体提取功能
                analysis_result = self.deep_analyzer.analyze_segment_entities(text)
                if analysis_result and 'entities' in analysis_result:
                    entities_data = analysis_result['entities']

                    # 转换为统一格式
                    extracted_entities = []
                    for entity_type in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']:
                        if entity_type in entities_data:
                            for entity_name in entities_data[entity_type]:
                                extracted_entities.append({
                                    'name': entity_name,
                                    'type': entity_type.rstrip('s'),  # 去掉复数形式
                                    'confidence': 0.9  # AI提取的置信度较高
                                })

                    return extracted_entities
            except Exception as e:
                logger.warning(f"AI实体提取失败，使用规则方法: {e}")

        # 回退到规则方法
        return self._extract_entities_by_rules(text)

    def _extract_entities_by_rules(self, text: str) -> List[Dict[str, Any]]:
        """基于规则的实体提取（回退方法）"""
        entities = []

        # 简单的中文人名识别
        import re

        # 中文姓氏模式
        chinese_surnames = [
            '习', '毛', '邓', '胡', '江', '温', '李', '王', '张', '刘', '陈', '杨', '赵', '黄', '周', '吴',
            '徐', '孙', '朱', '马', '胡', '郭', '林', '何', '高', '梁', '郑', '罗', '宋', '谢', '唐', '韩'
        ]

        # 查找可能的人名
        for surname in chinese_surnames:
            pattern = f'{surname}[\\u4e00-\\u9fff]{{1,3}}'
            matches = re.findall(pattern, text)
            for match in matches:
                if len(match) >= 2 and len(match) <= 4:  # 合理的姓名长度
                    entities.append({
                        'name': match,
                        'type': 'person',
                        'confidence': 0.7
                    })

        # 查找国家名
        countries = ['中国', '美国', '日本', '韩国', '朝鲜', '英国', '法国', '德国', '俄国', '苏联', '缅甸', '泰国', '越南', '老挝']
        for country in countries:
            if country in text:
                entities.append({
                    'name': country,
                    'type': 'country',
                    'confidence': 0.8
                })

        # 查找组织机构
        organizations = ['国民党', '共产党', '政府', '军队', '警察', '民族', '部队']
        for org in organizations:
            if org in text:
                entities.append({
                    'name': org,
                    'type': 'organization',
                    'confidence': 0.7
                })

        return entities

    def _extract_topics_from_text(self, text: str) -> List[str]:
        """从文本中提取主题"""
        if not text or not text.strip():
            return []

        topics = []

        # 基于关键词的主题识别
        topic_keywords = {
            '历史': ['历史', '年代', '时期', '朝代', '古代', '近代', '现代'],
            '政治': ['政治', '政府', '党', '领导', '政策', '制度', '国家'],
            '军事': ['军事', '战争', '军队', '武器', '战斗', '作战', '防务'],
            '经济': ['经济', '贸易', '商业', '市场', '金融', '投资', '发展'],
            '社会': ['社会', '民族', '文化', '教育', '生活', '人民', '群众'],
            '地理': ['地理', '地区', '城市', '山', '河', '边境', '领土'],
            '国际关系': ['国际', '外交', '关系', '合作', '冲突', '条约', '协议']
        }

        for topic, keywords in topic_keywords.items():
            if any(keyword in text for keyword in keywords):
                topics.append(topic)

        return topics

    def _analyze_emotion(self, text: str) -> Optional[Dict[str, Any]]:
        """分析文本情感"""
        if not text or not text.strip():
            return None

        # 简单的情感词典方法
        positive_words = ['好', '优', '成功', '胜利', '发展', '进步', '繁荣', '和平', '合作']
        negative_words = ['坏', '差', '失败', '战争', '冲突', '危机', '问题', '困难', '破坏']
        neutral_words = ['说', '表示', '认为', '指出', '提到', '介绍', '描述']

        pos_count = sum(1 for word in positive_words if word in text)
        neg_count = sum(1 for word in negative_words if word in text)
        neu_count = sum(1 for word in neutral_words if word in text)

        total_count = pos_count + neg_count + neu_count
        if total_count == 0:
            return None

        # 确定主导情感
        if pos_count > neg_count and pos_count > neu_count:
            emotion_type = "positive"
            confidence = pos_count / total_count
        elif neg_count > pos_count and neg_count > neu_count:
            emotion_type = "negative"
            confidence = neg_count / total_count
        else:
            emotion_type = "neutral"
            confidence = neu_count / total_count if neu_count > 0 else 0.5

        return {
            "type": emotion_type,
            "score": confidence,
            "confidence": min(confidence, 0.8),  # 限制最大置信度
            "distribution": {
                "positive": pos_count / total_count if total_count > 0 else 0,
                "negative": neg_count / total_count if total_count > 0 else 0,
                "neutral": neu_count / total_count if total_count > 0 else 0
            }
        }

    def _calculate_importance_score(
        self,
        text: str,
        entities: List[Dict[str, Any]] = None,
        topics: List[str] = None,
        emotion: Dict[str, Any] = None
    ) -> float:
        """计算原子重要性评分"""
        if not text:
            return 0.0

        score = 0.5  # 基础分数

        # 文本长度因子（适中长度得分高）
        text_length = len(text)
        if 50 <= text_length <= 200:
            score += 0.1
        elif text_length > 200:
            score += 0.05

        # 实体因子
        if entities:
            entity_bonus = min(len(entities) * 0.05, 0.2)  # 最多加0.2分
            score += entity_bonus

        # 主题因子
        if topics:
            topic_bonus = min(len(topics) * 0.05, 0.15)  # 最多加0.15分
            score += topic_bonus

        # 情感因子（非中性情感加分）
        if emotion and emotion.get('type') != 'neutral':
            emotion_bonus = emotion.get('confidence', 0) * 0.1
            score += emotion_bonus

        # 关键词重要性加分
        important_keywords = [
            '重要', '关键', '核心', '主要', '重大', '突破', '历史', '首次', '第一'
        ]
        keyword_bonus = sum(0.02 for keyword in important_keywords if keyword in text)
        score += min(keyword_bonus, 0.1)

        # 限制评分范围
        return max(0.0, min(score, 1.0))

    def save_annotations(self, annotations: List[AtomAnnotation], output_path: Path):
        """保存标注结果到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 转换为可序列化的格式
        serializable_annotations = []
        for annotation in annotations:
            serializable_annotations.append(annotation.model_dump())

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_annotations, f, ensure_ascii=False, indent=2)

        logger.info(f"标注数据已保存到: {output_path}")

    def load_annotations(self, file_path: Path) -> List[AtomAnnotation]:
        """从文件加载标注结果"""
        if not file_path.exists():
            return []

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        annotations = [AtomAnnotation(**item) for item in data]
        logger.info(f"从文件加载了 {len(annotations)} 个标注")
        return annotations


def annotate_segment_atoms(
    segment_atoms: List[Any],
    segment_id: str,
    api_key: str = None,
    output_dir: Path = None
) -> List[AtomAnnotation]:
    """
    为段落中的所有原子进行标注的便利函数

    Args:
        segment_atoms: 段落原子列表
        segment_id: 段落ID
        api_key: Claude API密钥
        output_dir: 输出目录

    Returns:
        List[AtomAnnotation]: 标注结果
    """
    annotator = AtomAnnotator(api_key)
    annotations = annotator.annotate_atoms_batch(segment_atoms, segment_id=segment_id)

    if output_dir:
        output_file = output_dir / f"{segment_id}_atom_annotations.json"
        annotator.save_annotations(annotations, output_file)

    return annotations