# -*- coding: utf-8 -*-
"""
向量化服务 - 使用 OpenAI file_search API
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class VectorizationService:
    """OpenAI 向量化服务"""

    def __init__(self, data_dir: Path, openai_api_key: str):
        """
        初始化向量化服务

        Args:
            data_dir: 数据目录
            openai_api_key: OpenAI API Key
        """
        self.data_dir = data_dir
        self.openai_api_key = openai_api_key
        self.metadata_file = data_dir / "vectorization_metadata.json"
        self.system_prompt_file = data_dir / "system_prompt.txt"

        # 默认系统提示词
        self.default_system_prompt = (
            "你是纪录片叙事分析师。基于文件检索回答：\n"
            "1) 先检索 level=segment（top-8）定位相关片段；\n"
            "2) 在这些片段内检索 level=atom 作为证据；\n"
            "3) 每条结论后必须附 [ATOM_ID @ mm:ss–mm:ss]；\n"
            "4) 如需限定范围（视频/时间/人物/主题/情感），请先筛选再作答；\n"
            "5) 证据不足请直说并给出建议补检关键词。"
        )

        # 延迟导入 OpenAI（避免启动时就要求 API key）
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_api_key)
        except ImportError:
            raise ImportError("请安装 openai: pip install openai")

    def load_metadata(self) -> Dict[str, Any]:
        """加载向量化元数据"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_metadata(self, metadata: Dict[str, Any]):
        """保存向量化元数据"""
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def upload_knowledge_base(
        self,
        jsonl_file: Path,
        video_id: str,
        title: str = "",
        description: str = ""
    ) -> Dict[str, Any]:
        """
        上传知识库到 OpenAI 并创建 Vector Store

        Args:
            jsonl_file: JSONL 文件路径
            video_id: 视频ID
            title: 视频标题
            description: 视频描述

        Returns:
            上传结果（包含 file_id 和 vector_store_id）
        """
        logger.info(f"开始上传知识库: {jsonl_file}")

        try:
            # Step 1: 上传文件（用于 assistants）
            with open(jsonl_file, 'rb') as f:
                file_obj = self.client.files.create(
                    file=f,
                    purpose="assistants"
                )

            file_id = file_obj.id
            logger.info(f"文件上传成功，file_id: {file_id}")

            # Step 2: 创建 Vector Store
            vector_store_name = f"kb_{video_id}"
            vector_store = self.client.vector_stores.create(
                name=vector_store_name
            )
            vector_store_id = vector_store.id
            logger.info(f"Vector Store 创建成功，vector_store_id: {vector_store_id}")

            # Step 3: 将文件添加到 Vector Store 并等待索引完成
            logger.info(f"开始索引文件到 Vector Store...")
            file_batch = self.client.vector_stores.file_batches.create_and_poll(
                vector_store_id=vector_store_id,
                file_ids=[file_id]
            )

            # 检查索引状态
            if file_batch.status == "completed":
                logger.info(f"文件索引完成")
            elif file_batch.status == "failed":
                logger.error(f"文件索引失败")
                return {
                    "success": False,
                    "error": "文件索引失败"
                }
            else:
                logger.warning(f"文件索引状态异常: {file_batch.status}")

            # Step 4: 保存元数据
            metadata = self.load_metadata()
            metadata[video_id] = {
                "file_id": file_id,
                "vector_store_id": vector_store_id,
                "file_name": jsonl_file.name,
                "title": title or video_id,
                "description": description or "",
                "upload_time": datetime.now().isoformat(),
                "status": "indexed",
                "record_count": self._count_records(jsonl_file)
            }
            self.save_metadata(metadata)

            return {
                "success": True,
                "file_id": file_id,
                "vector_store_id": vector_store_id,
                "video_id": video_id,
                "record_count": metadata[video_id]["record_count"]
            }

        except Exception as e:
            logger.error(f"上传失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def search_knowledge_base(
        self,
        video_ids: list,  # 支持多个视频
        query: str,
        model: str = "gpt-4o",
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        在知识库中搜索（使用 OpenAI Chat Completions API with file_search）

        Args:
            video_ids: 视频ID列表（支持多视频联合检索）
            query: 查询语句
            model: 使用的模型（gpt-4o 或 gpt-4o-mini）
            max_tokens: 最大输出token数

        Returns:
            搜索结果
        """
        metadata = self.load_metadata()

        # 收集所有 vector_store_ids
        vector_store_ids = []
        for video_id in video_ids:
            if video_id not in metadata:
                return {
                    "success": False,
                    "error": f"视频 {video_id} 尚未向量化"
                }
            if "vector_store_id" not in metadata[video_id]:
                return {
                    "success": False,
                    "error": f"视频 {video_id} 未创建 Vector Store，请重新向量化"
                }
            vector_store_ids.append(metadata[video_id]["vector_store_id"])

        logger.info(f"在知识库中搜索 (vector_store_ids: {vector_store_ids}): {query}")

        try:
            # 加载系统提示词（从文件或使用默认）
            if self.system_prompt_file.exists():
                with open(self.system_prompt_file, 'r', encoding='utf-8') as f:
                    system_prompt = f.read()
                logger.info("使用自定义系统提示词")
            else:
                system_prompt = self.default_system_prompt
                logger.info("使用默认系统提示词")

            # 使用 Responses API 进行搜索
            resp = self.client.responses.create(
                model=model,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": vector_store_ids
                }],
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                max_output_tokens=max_tokens
            )

            # 提取回答内容和引用
            answer = ""
            annotations = []

            # 遍历 output 提取 message 内容和 annotations
            if hasattr(resp, 'output'):
                for item in resp.output:
                    # 只处理 type='message' 的输出项
                    if hasattr(item, 'type') and item.type == 'message':
                        if hasattr(item, 'content') and isinstance(item.content, list):
                            for content_item in item.content:
                                # 提取文本内容
                                if hasattr(content_item, 'type') and content_item.type == 'output_text':
                                    if hasattr(content_item, 'text') and content_item.text:
                                        answer = content_item.text

                                    # 提取引用信息
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

            # 如果上面没有提取到内容，使用 output_text 作为备选
            if not answer and hasattr(resp, 'output_text') and resp.output_text:
                answer = resp.output_text

            # 提取 token 使用量
            usage = {}
            if hasattr(resp, 'usage'):
                # Responses API 使用 input_tokens 和 output_tokens
                input_tokens = getattr(resp.usage, 'input_tokens', 0)
                output_tokens = getattr(resp.usage, 'output_tokens', 0)
                total_tokens = getattr(resp.usage, 'total_tokens', 0)

                usage = {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": total_tokens
                }

            return {
                "success": True,
                "answer": answer,
                "annotations": annotations,
                "model": model,
                "usage": usage
            }

        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def get_vectorization_status(self, video_id: str) -> Dict[str, Any]:
        """
        获取向量化状态

        Args:
            video_id: 视频ID

        Returns:
            状态信息
        """
        metadata = self.load_metadata()

        if video_id not in metadata:
            return {
                "vectorized": False,
                "video_id": video_id
            }

        info = metadata[video_id]
        return {
            "vectorized": True,
            "video_id": video_id,
            "file_id": info["file_id"],
            "vector_store_id": info.get("vector_store_id"),
            "file_name": info["file_name"],
            "upload_time": info["upload_time"],
            "record_count": info["record_count"],
            "status": info["status"]
        }

    def delete_knowledge_base(self, video_id: str) -> Dict[str, Any]:
        """
        删除知识库（包括文件和 Vector Store）

        Args:
            video_id: 视频ID

        Returns:
            删除结果
        """
        metadata = self.load_metadata()

        if video_id not in metadata:
            return {
                "success": False,
                "error": f"视频 {video_id} 未向量化"
            }

        file_id = metadata[video_id]["file_id"]
        vector_store_id = metadata[video_id].get("vector_store_id")
        logger.info(f"删除知识库 file_id: {file_id}, vector_store_id: {vector_store_id}")

        try:
            # 删除 Vector Store（如果存在）
            if vector_store_id:
                try:
                    self.client.vector_stores.delete(vector_store_id)
                    logger.info(f"Vector Store 已删除: {vector_store_id}")
                except Exception as vs_error:
                    logger.warning(f"删除 Vector Store 失败: {vs_error}")

            # 删除 OpenAI 文件
            self.client.files.delete(file_id)
            logger.info(f"文件已删除: {file_id}")

            # 删除元数据
            del metadata[video_id]
            self.save_metadata(metadata)

            return {
                "success": True,
                "video_id": video_id,
                "file_id": file_id,
                "vector_store_id": vector_store_id
            }

        except Exception as e:
            logger.error(f"删除失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _count_records(self, jsonl_file: Path) -> int:
        """统计 JSONL 文件记录数"""
        count = 0
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
