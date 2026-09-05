# -*- coding: utf-8 -*-
"""
两阶段生成器 - 强制 "先片段→后原子" 的检索策略
"""

import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class TwoPhaseGenerator:
    """两阶段生成器：强制先检索 segments，再在其中检索 atoms"""

    def __init__(
        self,
        vector_store_manager,
        local_index_manager,
        openai_api_key: str
    ):
        """
        初始化两阶段生成器

        Args:
            vector_store_manager: VectorStoreManager 实例
            local_index_manager: LocalIndexManager 实例
            openai_api_key: OpenAI API Key
        """
        self.vector_store_manager = vector_store_manager
        self.local_index_manager = local_index_manager
        self.openai_api_key = openai_api_key

        # 延迟导入 OpenAI
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_api_key)
        except ImportError:
            raise ImportError("请安装 openai: pip install openai")

        # Phase 1 系统提示词（只检索 segments）
        self.phase1_system_prompt = (
            "你是纪录片内容分析专家。当前任务：从片段级别的摘要中定位相关内容。\n"
            "要求：\n"
            "1) 你的知识库包含视频的片段摘要（segment-level summaries）；\n"
            "2) 分析用户查询，找出最相关的片段；\n"
            "3) 输出格式：返回相关片段的 ID 列表和简要描述；\n"
            "4) 不要展开具体细节，只需要指出哪些片段相关。\n"
            "示例输出格式：\n"
            "相关片段：\n"
            "- seg_1: 开场介绍主题\n"
            "- seg_5: 讨论核心论点\n"
        )

        # Phase 2 系统提示词（只检索 atoms）
        self.phase2_system_prompt_template = (
            "你是纪录片精细内容分析专家。当前任务：从原子级别的内容中提取精确证据。\n"
            "背景：用户查询与以下片段相关：{segment_context}\n"
            "\n"
            "**严格要求**：\n"
            "1) 你的知识库包含这些片段内的原子级内容（atom-level content），metadata.level='atom'；\n"
            "2) 找出用户查询的具体证据，每个论点都必须有证据支持；\n"
            "3) **必须**在每条论点后附上引用：[ATOM_ID @ HH:MM:SS–HH:MM:SS]；\n"
            "4) 引用格式严格遵守：[数字或compound_X_Y @ 时间范围]；\n"
            "5) 时间格式必须是 HH:MM:SS，例如 00:01:23；\n"
            "6) 每个段落至少包含1-2条引用；\n"
            "7) 如果证据不足，明确说明。\n"
            "\n"
            "示例输出格式：\n"
            "主持人在开场提到气候变化是核心议题 [42 @ 00:01:23–00:01:45]。随后展示了相关数据 [43 @ 00:01:46–00:02:10]。\n"
            "\n"
            "**注意**：没有引用的论点将被视为无效！\n"
        )

    def generate_two_phase(
        self,
        video_id: str,
        query: str,
        model: str = "gpt-4o",
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        两阶段生成：使用单一 Vector Store，通过 prompt 指导过滤 level

        Args:
            video_id: 视频ID
            query: 用户查询
            model: 使用的模型
            max_tokens: 最大输出token数

        Returns:
            包含两阶段结果的字典
        """
        logger.info(f"开始两阶段生成: video_id={video_id}, query={query}")

        # 获取 Vector Store ID
        status = self.vector_store_manager.get_status(video_id)
        if not status.get("vectorized"):
            return {
                "success": False,
                "error": f"视频 {video_id} 尚未向量化"
            }

        vector_store_id = status["vector_store_id"]

        # ==================== Phase 1: 只关注 segment 级别 ====================
        logger.info("Phase 1: 检索相关片段（segment level）...")

        # 在 Phase 1 提示词中明确指示只关注 segment 级别的内容
        phase1_enhanced_prompt = (
            self.phase1_system_prompt +
            "\n重要：只关注 metadata.level='segment' 的记录，忽略 atom 级别的内容。"
        )

        try:
            phase1_resp = self.client.responses.create(
                model=model,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id]
                }],
                input=[
                    {"role": "system", "content": phase1_enhanced_prompt},
                    {"role": "user", "content": query}
                ],
                max_output_tokens=1000  # Phase 1 不需要太多token
            )

            phase1_answer = self._extract_answer_from_response(phase1_resp)
            logger.info(f"Phase 1 完成: {phase1_answer[:100]}...")

        except Exception as e:
            logger.error(f"Phase 1 失败: {e}")
            return {
                "success": False,
                "error": f"Phase 1 失败: {str(e)}"
            }

        # ==================== Phase 2: 只关注 atom 级别 ====================
        logger.info("Phase 2: 在相关片段内检索原子内容（atom level）...")

        # 构建 Phase 2 系统提示词（包含 Phase 1 的上下文 + 明确指示只看 atom）
        segment_context = phase1_answer
        phase2_system_prompt = self.phase2_system_prompt_template.format(
            segment_context=segment_context
        )
        phase2_enhanced_prompt = (
            phase2_system_prompt +
            "\n重要：只关注 metadata.level='atom' 的记录，这些是最细粒度的内容片段。"
        )

        try:
            phase2_resp = self.client.responses.create(
                model=model,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id]
                }],
                input=[
                    {"role": "system", "content": phase2_enhanced_prompt},
                    {"role": "user", "content": query}
                ],
                max_output_tokens=max_tokens
            )

            phase2_answer = self._extract_answer_from_response(phase2_resp)
            phase2_annotations = self._extract_annotations_from_response(phase2_resp)

            logger.info(f"Phase 2 完成，生成答案长度: {len(phase2_answer)} 字符")

        except Exception as e:
            logger.error(f"Phase 2 失败: {e}")
            return {
                "success": False,
                "error": f"Phase 2 失败: {str(e)}"
            }

        # ==================== 提取 Token 使用量 ====================
        phase1_usage = self._extract_usage(phase1_resp)
        phase2_usage = self._extract_usage(phase2_resp)

        total_usage = {
            "prompt_tokens": phase1_usage.get("prompt_tokens", 0) + phase2_usage.get("prompt_tokens", 0),
            "completion_tokens": phase1_usage.get("completion_tokens", 0) + phase2_usage.get("completion_tokens", 0),
            "total_tokens": phase1_usage.get("total_tokens", 0) + phase2_usage.get("total_tokens", 0)
        }

        return {
            "success": True,
            "phase1": {
                "answer": phase1_answer,
                "usage": phase1_usage
            },
            "phase2": {
                "answer": phase2_answer,
                "annotations": phase2_annotations,
                "usage": phase2_usage
            },
            "final_answer": phase2_answer,  # 最终答案使用 Phase 2 的输出
            "model": model,
            "usage": total_usage
        }

    def _extract_answer_from_response(self, resp) -> str:
        """从 Responses API 响应中提取答案文本"""
        answer = ""

        if hasattr(resp, 'output'):
            for item in resp.output:
                if hasattr(item, 'type') and item.type == 'message':
                    if hasattr(item, 'content') and isinstance(item.content, list):
                        for content_item in item.content:
                            if hasattr(content_item, 'type') and content_item.type == 'output_text':
                                if hasattr(content_item, 'text') and content_item.text:
                                    answer = content_item.text

        # 备选方案
        if not answer and hasattr(resp, 'output_text') and resp.output_text:
            answer = resp.output_text

        return answer

    def _extract_annotations_from_response(self, resp) -> list:
        """从 Responses API 响应中提取引用信息"""
        annotations = []

        if hasattr(resp, 'output'):
            for item in resp.output:
                if hasattr(item, 'type') and item.type == 'message':
                    if hasattr(item, 'content') and isinstance(item.content, list):
                        for content_item in item.content:
                            if hasattr(content_item, 'type') and content_item.type == 'output_text':
                                if hasattr(content_item, 'annotations') and content_item.annotations:
                                    for ann in content_item.annotations:
                                        annotation_dict = {
                                            "type": getattr(ann, 'type', 'unknown')
                                        }
                                        if hasattr(ann, 'file_id'):
                                            annotation_dict["file_id"] = ann.file_id
                                        if hasattr(ann, 'filename'):
                                            annotation_dict["filename"] = ann.filename
                                        if hasattr(ann, 'index'):
                                            annotation_dict["index"] = ann.index
                                        annotations.append(annotation_dict)

        return annotations

    def _extract_usage(self, resp) -> Dict[str, int]:
        """提取 token 使用量"""
        usage = {}

        if hasattr(resp, 'usage'):
            input_tokens = getattr(resp.usage, 'input_tokens', 0)
            output_tokens = getattr(resp.usage, 'output_tokens', 0)
            total_tokens = getattr(resp.usage, 'total_tokens', 0)

            usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens
            }

        return usage
