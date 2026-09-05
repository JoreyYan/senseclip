# -*- coding: utf-8 -*-
"""
统一 Agent - 智能路由到 QA 或 Cutlist 模式
"""

import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class UnifiedAgent:
    """统一 Agent：根据意图自动路由到 QA 或 Cutlist 生成"""

    def __init__(
        self,
        vector_store_manager,
        local_index_manager,
        intent_classifier,
        openai_api_key: str
    ):
        """
        初始化统一 Agent

        Args:
            vector_store_manager: VectorStoreManager 实例
            local_index_manager: LocalIndexManager 实例
            intent_classifier: IntentClassifier 实例
            openai_api_key: OpenAI API Key
        """
        self.vector_store_manager = vector_store_manager
        self.local_index_manager = local_index_manager
        self.intent_classifier = intent_classifier
        self.openai_api_key = openai_api_key

        # 初始化 CitationExtractor
        from api.citation_extractor import CitationExtractor
        self.citation_extractor = CitationExtractor(local_index_manager)

        # 延迟导入 OpenAI
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_api_key)
        except ImportError:
            raise ImportError("请安装 openai: pip install openai")

        # === QA 模式的提示词 ===
        self.qa_phase1_system_prompt = (
            "你是纪录片内容分析专家。当前任务：从片段级别的摘要中定位相关内容。\n"
            "要求：\n"
            "1) 你的知识库包含视频的片段摘要（segment-level summaries）；\n"
            "2) 分析用户查询，找出最相关的片段；\n"
            "3) 输出格式：返回相关片段的 ID 列表和简要描述；\n"
            "4) 不要展开具体细节，只需要指出哪些片段相关。\n"
        )

        self.qa_phase2_system_prompt_template = (
            "You are a documentary content analysis expert. Task: Provide detailed answers with precise citations.\n"
            "Context: User query relates to these segments: {segment_context}\n"
            "\n"
            "**CITATION FORMAT (MANDATORY)**:\n"
            "- Format: [ATOM_ID @ HH:MM:SS-HH:MM:SS]\n"
            "- ATOM_ID: number (e.g. 42) or compound_X_Y format\n"
            "- Time: Must be HH:MM:SS format, e.g. 00:01:23\n"
            "- Example: [42 @ 00:01:23-00:01:45]\n"
            "\n"
            "**OUTPUT REQUIREMENTS (NON-NEGOTIABLE)**:\n"
            "1. EVERY factual statement MUST have a citation in [ATOM_ID @ time] format\n"
            "2. Citations MUST immediately follow the related statement\n"
            "3. Each paragraph needs AT LEAST 2-3 citations\n"
            "4. If no evidence in knowledge base, explicitly state 'Cannot find this information in the video'\n"
            "5. Answer style: Natural and conversational, but citation format must be strict\n"
            "6. Your knowledge base contains atom-level content with metadata.level='atom'\n"
            "\n"
            "**CORRECT EXAMPLE**:\n"
            "Khun Sa was born in 1934 on the China-Myanmar border [125 @ 00:15:30-00:15:45], and worked as a cattle herder in his early years [126 @ 00:15:46-00:16:10].\n"
            "He later rose to power through military means [128 @ 00:18:20-00:18:55] and became the largest drug lord in the Golden Triangle [130 @ 00:19:15-00:19:40].\n"
            "\n"
            "**INCORRECT EXAMPLE (FORBIDDEN)**:\n"
            "Khun Sa was born in 1934 on the China-Myanmar border and worked as a cattle herder. He became the largest drug lord. (MISSING CITATIONS)\n"
            "\n"
            "**IMPORTANT REMINDERS**:\n"
            "- Statements without citations are invalid\n"
            "- Must extract citations from atom-level content in knowledge base\n"
            "- Do not fabricate ATOM_ID or time ranges\n"
            "- If information not in knowledge base, be honest\n"
        )

        # === Cutlist 模式的提示词 ===
        self.cutlist_phase1_system_prompt = (
            "你是纪录片剪辑专家。当前任务：从片段级别的摘要中定位剪辑需要的内容。\n"
            "要求：\n"
            "1) 你的知识库包含视频的片段摘要（segment-level summaries）；\n"
            "2) 分析用户的剪辑需求（主题、时长、风格）；\n"
            "3) 找出所有与剪辑主题相关的片段；\n"
            "4) 输出格式：返回相关片段的 ID 列表和简要描述。\n"
        )

        self.cutlist_phase2_system_prompt_template = (
            "你是纪录片剪辑专家。当前任务：生成结构化的剪辑方案（Cutlist）。\n"
            "背景：用户剪辑需求与以下片段相关：{segment_context}\n"
            "\n"
            "**剪辑需求**：{original_query}\n"
            "\n"
            "**严格要求**：\n"
            "1) 你的知识库包含原子级内容（atom-level content），metadata.level='atom'；\n"
            "2) 从原子库中精选最符合剪辑主题的片段；\n"
            "3) 按照逻辑顺序组织片段（开场→展开→高潮→结尾）；\n"
            "4) **必须**输出 JSON 格式的剪辑方案；\n"
            "5) 每个片段必须包含：atom_id, 时间范围, 内容描述, 剪辑理由；\n"
            "6) 总时长应该接近用户要求的时长（如果指定）；\n"
            "7) 时间格式必须是 HH:MM:SS。\n"
            "\n"
            "**输出格式示例**：\n"
            "```json\n"
            "{{\n"
            '  "title": "坤沙：从放牛娃到金三角霸主",\n'
            '  "target_duration": "25:00",\n'
            '  "segments": [\n'
            "    {{\n"
            '      "sequence": 1,\n'
            '      "title": "边境童年",\n'
            '      "atoms": [\n'
            "        {{\n"
            '          "atom_id": "42",\n'
            '          "time_range": "00:05:12–00:05:45",\n'
            '          "content": "坤沙出生在中缅边境...",\n'
            '          "reason": "开场引入主角背景"\n'
            "        }}\n"
            "      ],\n"
            '      "duration": "5:30"\n'
            "    }}\n"
            "  ],\n"
            '  "total_duration": "25:00"\n'
            "}}\n"
            "```\n"
            "\n"
            "**注意**：输出必须是严格的 JSON 格式，不要有其他解释文字！\n"
        )

    def chat(
        self,
        video_id: str,
        query: str,
        model: str = "gpt-4o",
        max_tokens: int = 2000,
        force_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        统一聊天接口 - 自动路由到 QA 或 Cutlist 模式

        Args:
            video_id: 视频ID
            query: 用户查询
            model: 使用的模型
            max_tokens: 最大输出token数
            force_mode: 强制模式（"qa" 或 "cutlist"），如果为 None 则自动判断

        Returns:
            包含结果的字典
        """
        logger.info(f"统一 Agent 收到请求: video_id={video_id}, query={query}")

        # Step 1: 意图分类
        if force_mode:
            intent = force_mode
            confidence = 1.0
            reason = "手动指定"
            logger.info(f"强制模式: {intent}")
        else:
            intent_result = self.intent_classifier.classify_with_confidence(query)
            intent = intent_result["intent"]
            confidence = intent_result["confidence"]
            reason = intent_result["reason"]
            logger.info(f"意图分类: {intent} (置信度: {confidence:.2%}, 原因: {reason})")

        # Step 2: 检查 Vector Store
        status = self.vector_store_manager.get_status(video_id)
        if not status.get("vectorized"):
            return {
                "success": False,
                "error": f"视频 {video_id} 尚未向量化"
            }

        vector_store_id = status["vector_store_id"]

        # Step 3: 根据意图路由
        if intent == "qa":
            result = self._generate_qa(video_id, query, vector_store_id, model, max_tokens)
        else:  # cutlist
            result = self._generate_cutlist(video_id, query, vector_store_id, model, max_tokens)

        # Step 4: 附加元数据
        result["intent"] = intent
        result["intent_confidence"] = confidence
        result["intent_reason"] = reason

        return result

    def _generate_qa(
        self,
        video_id: str,
        query: str,
        vector_store_id: str,
        model: str,
        max_tokens: int
    ) -> Dict[str, Any]:
        """QA 模式生成"""
        logger.info("执行 QA 模式生成...")

        # Phase 1: Segment-level retrieval
        phase1_enhanced_prompt = (
            self.qa_phase1_system_prompt +
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
                max_output_tokens=1000
            )

            phase1_answer = self._extract_answer_from_response(phase1_resp)
            logger.info(f"Phase 1 完成: {phase1_answer[:100]}...")

        except Exception as e:
            logger.error(f"QA Phase 1 失败: {e}")
            return {
                "success": False,
                "error": f"QA Phase 1 失败: {str(e)}",
                "mode": "qa"
            }

        # Phase 2: Atom-level retrieval
        segment_context = phase1_answer
        phase2_system_prompt = self.qa_phase2_system_prompt_template.format(
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
            logger.error(f"QA Phase 2 失败: {e}")
            return {
                "success": False,
                "error": f"QA Phase 2 失败: {str(e)}",
                "mode": "qa"
            }

        # Note: Citations are a known limitation with file_search API
        # The API doesn't expose which specific atoms were retrieved
        citations = []
        citation_validation = {"valid_count": 0, "invalid_count": 0}

        # 提取 Token 使用量
        phase1_usage = self._extract_usage(phase1_resp)
        phase2_usage = self._extract_usage(phase2_resp)

        total_usage = {
            "prompt_tokens": phase1_usage.get("prompt_tokens", 0) + phase2_usage.get("prompt_tokens", 0),
            "completion_tokens": phase1_usage.get("completion_tokens", 0) + phase2_usage.get("completion_tokens", 0),
            "total_tokens": phase1_usage.get("total_tokens", 0) + phase2_usage.get("total_tokens", 0)
        }

        return {
            "success": True,
            "mode": "qa",
            "answer": phase2_answer,
            "citations": citations,  # 新增：引用列表
            "citation_validation": citation_validation,  # 新增：引用验证结果
            "phase1": {
                "answer": phase1_answer,
                "usage": phase1_usage
            },
            "phase2": {
                "answer": phase2_answer,
                "annotations": phase2_annotations,
                "usage": phase2_usage
            },
            "model": model,
            "usage": total_usage
        }

    def _generate_cutlist(
        self,
        video_id: str,
        query: str,
        vector_store_id: str,
        model: str,
        max_tokens: int
    ) -> Dict[str, Any]:
        """Cutlist 模式生成"""
        logger.info("执行 Cutlist 模式生成...")

        # Phase 1: Segment-level retrieval
        phase1_enhanced_prompt = (
            self.cutlist_phase1_system_prompt +
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
                max_output_tokens=1000
            )

            phase1_answer = self._extract_answer_from_response(phase1_resp)
            logger.info(f"Phase 1 完成: {phase1_answer[:100]}...")

        except Exception as e:
            logger.error(f"Cutlist Phase 1 失败: {e}")
            return {
                "success": False,
                "error": f"Cutlist Phase 1 失败: {str(e)}",
                "mode": "cutlist"
            }

        # Phase 2: Atom-level retrieval + Cutlist generation
        segment_context = phase1_answer
        phase2_system_prompt = self.cutlist_phase2_system_prompt_template.format(
            segment_context=segment_context,
            original_query=query
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

            # 尝试解析 JSON
            cutlist = self._extract_cutlist_json(phase2_answer)

        except Exception as e:
            logger.error(f"Cutlist Phase 2 失败: {e}")
            return {
                "success": False,
                "error": f"Cutlist Phase 2 失败: {str(e)}",
                "mode": "cutlist"
            }

        # 提取 Token 使用量
        phase1_usage = self._extract_usage(phase1_resp)
        phase2_usage = self._extract_usage(phase2_resp)

        total_usage = {
            "prompt_tokens": phase1_usage.get("prompt_tokens", 0) + phase2_usage.get("prompt_tokens", 0),
            "completion_tokens": phase1_usage.get("completion_tokens", 0) + phase2_usage.get("completion_tokens", 0),
            "total_tokens": phase1_usage.get("total_tokens", 0) + phase2_usage.get("total_tokens", 0)
        }

        return {
            "success": True,
            "mode": "cutlist",
            "cutlist": cutlist,
            "raw_output": phase2_answer,
            "phase1": {
                "answer": phase1_answer,
                "usage": phase1_usage
            },
            "phase2": {
                "answer": phase2_answer,
                "annotations": phase2_annotations,
                "usage": phase2_usage
            },
            "model": model,
            "usage": total_usage
        }

    def _extract_cutlist_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从文本中提取 JSON 格式的 Cutlist"""
        try:
            # 尝试直接解析
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 代码块
            import re
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

            # 尝试查找第一个 { 到最后一个 }
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                try:
                    return json.loads(text[start_idx:end_idx+1])
                except json.JSONDecodeError:
                    pass

            logger.warning("无法从输出中提取 JSON 格式的 Cutlist")
            return None

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
