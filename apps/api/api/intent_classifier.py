# -*- coding: utf-8 -*-
"""
意图分类器 - 判断用户查询是 QA 还是 Cutlist 生成请求
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

IntentType = Literal["qa", "cutlist"]


class IntentClassifier:
    """意图分类器：判断用户意图（QA vs Cutlist）"""

    def __init__(self, openai_api_key: str = None):
        """
        初始化意图分类器

        Args:
            openai_api_key: OpenAI API Key（可选，用于基于 LLM 的分类）
        """
        self.openai_api_key = openai_api_key

        # Cutlist 关键词（中文）
        self.cutlist_keywords = [
            "做", "生成", "制作", "创建", "编辑", "剪辑",
            "剪", "切", "裁", "合成", "拼接", "整理",
            "分钟", "秒", "小时", "时长",
            "视频", "片段", "集锦", "精选", "合集"
        ]

        # QA 关键词（中文）
        self.qa_keywords = [
            "什么", "谁", "哪", "为什么", "怎么", "如何",
            "是", "吗", "呢", "讲", "说", "提到", "介绍",
            "内容", "主题", "话题", "观点", "论点"
        ]

    def classify(self, query: str) -> IntentType:
        """
        分类用户查询意图

        Args:
            query: 用户查询文本

        Returns:
            "qa" 或 "cutlist"
        """
        query_lower = query.lower()

        # 方法 1: 基于关键词的规则分类
        cutlist_score = sum(1 for kw in self.cutlist_keywords if kw in query_lower)
        qa_score = sum(1 for kw in self.qa_keywords if kw in query_lower)

        # 规则 1: 如果包含 "做" "生成" "制作" 等动词 + 时长描述 → Cutlist
        has_action_verb = any(kw in query_lower for kw in ["做", "生成", "制作", "创建", "剪辑", "编辑"])
        has_duration = any(kw in query_lower for kw in ["分钟", "秒", "小时", "时长"])

        if has_action_verb and has_duration:
            logger.info(f"意图分类: Cutlist (动词+时长) - '{query[:50]}...'")
            return "cutlist"

        # 规则 2: 如果包含 "做" "生成" 等动词 + "视频" "剪辑" 等名词 → Cutlist
        has_video_noun = any(kw in query_lower for kw in ["视频", "片段", "剪辑", "集锦", "精选", "合集"])
        if has_action_verb and has_video_noun:
            logger.info(f"意图分类: Cutlist (动词+视频名词) - '{query[:50]}...'")
            return "cutlist"

        # 规则 2.5: 如果包含"剪辑方案"、"cutlist"、"剪辑建议"等明确的剪辑意图词 → Cutlist
        explicit_cutlist_phrases = ["剪辑方案", "cutlist", "剪辑建议", "剪辑计划", "剪辑策略", "剪成", "分成几个视频", "上传"]
        if any(phrase in query_lower for phrase in explicit_cutlist_phrases):
            logger.info(f"意图分类: Cutlist (明确剪辑意图) - '{query[:50]}...'")
            return "cutlist"

        # 规则 3: 如果 cutlist 关键词明显多于 qa 关键词 → Cutlist
        if cutlist_score > qa_score and cutlist_score >= 2:
            logger.info(f"意图分类: Cutlist (关键词权重: {cutlist_score} vs {qa_score}) - '{query[:50]}...'")
            return "cutlist"

        # 默认: QA
        logger.info(f"意图分类: QA (默认) - '{query[:50]}...'")
        return "qa"

    def classify_with_confidence(self, query: str) -> dict:
        """
        分类并返回置信度

        Args:
            query: 用户查询文本

        Returns:
            {
                "intent": "qa" | "cutlist",
                "confidence": float (0-1),
                "reason": str
            }
        """
        query_lower = query.lower()

        cutlist_score = sum(1 for kw in self.cutlist_keywords if kw in query_lower)
        qa_score = sum(1 for kw in self.qa_keywords if kw in query_lower)

        has_action_verb = any(kw in query_lower for kw in ["做", "生成", "制作", "创建", "剪辑", "编辑"])
        has_duration = any(kw in query_lower for kw in ["分钟", "秒", "小时", "时长"])
        has_video_noun = any(kw in query_lower for kw in ["视频", "片段", "剪辑", "集锦", "精选", "合集"])

        # 高置信度 Cutlist
        if has_action_verb and has_duration:
            return {
                "intent": "cutlist",
                "confidence": 0.95,
                "reason": "包含动作动词和时长描述"
            }

        if has_action_verb and has_video_noun:
            return {
                "intent": "cutlist",
                "confidence": 0.90,
                "reason": "包含动作动词和视频相关名词"
            }

        # 中置信度 Cutlist
        if cutlist_score > qa_score and cutlist_score >= 2:
            confidence = min(0.70 + cutlist_score * 0.05, 0.85)
            return {
                "intent": "cutlist",
                "confidence": confidence,
                "reason": f"Cutlist 关键词权重较高 ({cutlist_score} vs {qa_score})"
            }

        # 高置信度 QA
        if qa_score >= 2 and cutlist_score == 0:
            return {
                "intent": "qa",
                "confidence": 0.90,
                "reason": "包含多个疑问词且无剪辑意图"
            }

        # 默认 QA（中低置信度）
        return {
            "intent": "qa",
            "confidence": 0.60,
            "reason": "默认为 QA 模式"
        }


# 全局实例（可选）
_intent_classifier_instance = None


def get_intent_classifier(openai_api_key: str = None) -> IntentClassifier:
    """获取全局意图分类器实例"""
    global _intent_classifier_instance
    if _intent_classifier_instance is None:
        _intent_classifier_instance = IntentClassifier(openai_api_key)
    return _intent_classifier_instance
