# -*- coding: utf-8 -*-
"""
双 Vector Store 管理器
分别管理 segments 和 atoms 的向量存储
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class VectorStoreManager:
    """双 Vector Store 管理器"""

    def __init__(self, data_dir: Path, openai_api_key: str):
        """
        初始化 Vector Store 管理器（兼容单/双 Vector Store）

        Args:
            data_dir: 数据目录
            openai_api_key: OpenAI API Key
        """
        self.data_dir = data_dir
        self.openai_api_key = openai_api_key
        # 优先读取单 VS 元数据（新格式）
        self.single_vs_metadata_file = data_dir / "vectorization_metadata.json"
        # 备用：双 VS 元数据（旧格式）
        self.dual_vs_metadata_file = data_dir / "dual_vector_store_metadata.json"

        # 延迟导入 OpenAI
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_api_key)
        except ImportError:
            raise ImportError("请安装 openai: pip install openai")

    def load_metadata(self) -> Dict[str, Any]:
        """加载双 Vector Store 元数据（旧格式）"""
        if self.dual_vs_metadata_file.exists():
            with open(self.dual_vs_metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_metadata(self, metadata: Dict[str, Any]):
        """保存双 Vector Store 元数据（旧格式）"""
        with open(self.dual_vs_metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def upload_dual_knowledge_base(
        self,
        video_id: str,
        segments_file: Path,
        atoms_file: Path,
        title: str = "",
        description: str = ""
    ) -> Dict[str, Any]:
        """
        上传双知识库（segments 和 atoms）

        Args:
            video_id: 视频ID
            segments_file: segments.jsonl 文件路径
            atoms_file: atoms.jsonl 文件路径
            title: 视频标题
            description: 视频描述

        Returns:
            上传结果
        """
        logger.info(f"开始上传双知识库: video_id={video_id}")

        try:
            # Step 1: 上传 segments 文件
            logger.info(f"上传 segments 文件: {segments_file}")
            with open(segments_file, 'rb') as f:
                segments_file_obj = self.client.files.create(
                    file=f,
                    purpose="assistants"
                )
            segments_file_id = segments_file_obj.id
            logger.info(f"Segments 文件上传成功: {segments_file_id}")

            # Step 2: 上传 atoms 文件
            logger.info(f"上传 atoms 文件: {atoms_file}")
            with open(atoms_file, 'rb') as f:
                atoms_file_obj = self.client.files.create(
                    file=f,
                    purpose="assistants"
                )
            atoms_file_id = atoms_file_obj.id
            logger.info(f"Atoms 文件上传成功: {atoms_file_id}")

            # Step 3: 创建 Segments Vector Store
            segments_vs_name = f"kb_{video_id}_segments"
            segments_vs = self.client.beta.vector_stores.create(
                name=segments_vs_name
            )
            segments_vs_id = segments_vs.id
            logger.info(f"Segments Vector Store 创建成功: {segments_vs_id}")

            # Step 4: 创建 Atoms Vector Store
            atoms_vs_name = f"kb_{video_id}_atoms"
            atoms_vs = self.client.beta.vector_stores.create(
                name=atoms_vs_name
            )
            atoms_vs_id = atoms_vs.id
            logger.info(f"Atoms Vector Store 创建成功: {atoms_vs_id}")

            # Step 5: 将 segments 文件添加到 Segments Vector Store
            logger.info("索引 segments 文件...")
            segments_batch = self.client.beta.vector_stores.file_batches.create_and_poll(
                vector_store_id=segments_vs_id,
                file_ids=[segments_file_id]
            )

            if segments_batch.status != "completed":
                logger.error(f"Segments 索引失败: {segments_batch.status}")
                return {
                    "success": False,
                    "error": f"Segments 索引失败: {segments_batch.status}"
                }

            # Step 6: 将 atoms 文件添加到 Atoms Vector Store
            logger.info("索引 atoms 文件...")
            atoms_batch = self.client.beta.vector_stores.file_batches.create_and_poll(
                vector_store_id=atoms_vs_id,
                file_ids=[atoms_file_id]
            )

            if atoms_batch.status != "completed":
                logger.error(f"Atoms 索引失败: {atoms_batch.status}")
                return {
                    "success": False,
                    "error": f"Atoms 索引失败: {atoms_batch.status}"
                }

            # Step 7: 保存元数据
            metadata = self.load_metadata()
            metadata[video_id] = {
                "segments": {
                    "file_id": segments_file_id,
                    "vector_store_id": segments_vs_id,
                    "file_name": segments_file.name,
                    "record_count": self._count_records(segments_file)
                },
                "atoms": {
                    "file_id": atoms_file_id,
                    "vector_store_id": atoms_vs_id,
                    "file_name": atoms_file.name,
                    "record_count": self._count_records(atoms_file)
                },
                "title": title or video_id,
                "description": description or "",
                "upload_time": datetime.now().isoformat(),
                "status": "indexed"
            }
            self.save_metadata(metadata)

            logger.info("双知识库上传完成")
            return {
                "success": True,
                "video_id": video_id,
                "segments": {
                    "file_id": segments_file_id,
                    "vector_store_id": segments_vs_id,
                    "record_count": metadata[video_id]["segments"]["record_count"]
                },
                "atoms": {
                    "file_id": atoms_file_id,
                    "vector_store_id": atoms_vs_id,
                    "record_count": metadata[video_id]["atoms"]["record_count"]
                }
            }

        except Exception as e:
            logger.error(f"上传双知识库失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def get_vector_store_ids(self, video_id: str) -> Optional[Dict[str, str]]:
        """
        获取双 Vector Store IDs

        Args:
            video_id: 视频ID

        Returns:
            包含 segments_vs_id 和 atoms_vs_id 的字典，如果不存在返回 None
        """
        metadata = self.load_metadata()

        if video_id not in metadata:
            return None

        video_meta = metadata[video_id]
        return {
            "segments_vs_id": video_meta["segments"]["vector_store_id"],
            "atoms_vs_id": video_meta["atoms"]["vector_store_id"]
        }

    def get_status(self, video_id: str) -> Dict[str, Any]:
        """
        获取 Vector Store 状态（兼容单/双 Vector Store）

        Args:
            video_id: 视频ID

        Returns:
            状态信息
        """
        # 优先读取单 VS 元数据（新格式）
        if self.single_vs_metadata_file.exists():
            with open(self.single_vs_metadata_file, 'r', encoding='utf-8') as f:
                single_vs_metadata = json.load(f)

            if video_id in single_vs_metadata:
                video_meta = single_vs_metadata[video_id]
                return {
                    "vectorized": True,
                    "video_id": video_id,
                    "vector_store_id": video_meta["vector_store_id"],
                    "file_id": video_meta["file_id"],
                    "file_ids": [video_meta["file_id"]],
                    "title": video_meta.get("title", ""),
                    "description": video_meta.get("description", ""),
                    "upload_time": video_meta.get("upload_time", ""),
                    "status": video_meta.get("status", ""),
                    "record_count": video_meta.get("record_count", 0),
                    "mode": "single_vector_store"
                }

        # 备用：读取双 VS 元数据（旧格式）
        metadata = self.load_metadata()

        if video_id not in metadata:
            return {
                "vectorized": False,
                "video_id": video_id
            }

        video_meta = metadata[video_id]
        return {
            "vectorized": True,
            "video_id": video_id,
            "title": video_meta.get("title", ""),
            "description": video_meta.get("description", ""),
            "upload_time": video_meta.get("upload_time", ""),
            "status": video_meta.get("status", ""),
            "segments": {
                "file_id": video_meta["segments"]["file_id"],
                "vector_store_id": video_meta["segments"]["vector_store_id"],
                "record_count": video_meta["segments"]["record_count"]
            },
            "atoms": {
                "file_id": video_meta["atoms"]["file_id"],
                "vector_store_id": video_meta["atoms"]["vector_store_id"],
                "record_count": video_meta["atoms"]["record_count"]
            },
            "mode": "dual_vector_store"
        }

    def delete_knowledge_base(self, video_id: str) -> Dict[str, Any]:
        """
        删除双知识库（包括文件和 Vector Stores）

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

        video_meta = metadata[video_id]
        logger.info(f"删除双知识库: video_id={video_id}")

        try:
            # 删除 Segments Vector Store
            segments_vs_id = video_meta["segments"]["vector_store_id"]
            try:
                self.client.beta.vector_stores.delete(segments_vs_id)
                logger.info(f"Segments Vector Store 已删除: {segments_vs_id}")
            except Exception as e:
                logger.warning(f"删除 Segments Vector Store 失败: {e}")

            # 删除 Atoms Vector Store
            atoms_vs_id = video_meta["atoms"]["vector_store_id"]
            try:
                self.client.beta.vector_stores.delete(atoms_vs_id)
                logger.info(f"Atoms Vector Store 已删除: {atoms_vs_id}")
            except Exception as e:
                logger.warning(f"删除 Atoms Vector Store 失败: {e}")

            # 删除 Segments 文件
            segments_file_id = video_meta["segments"]["file_id"]
            try:
                self.client.files.delete(segments_file_id)
                logger.info(f"Segments 文件已删除: {segments_file_id}")
            except Exception as e:
                logger.warning(f"删除 Segments 文件失败: {e}")

            # 删除 Atoms 文件
            atoms_file_id = video_meta["atoms"]["file_id"]
            try:
                self.client.files.delete(atoms_file_id)
                logger.info(f"Atoms 文件已删除: {atoms_file_id}")
            except Exception as e:
                logger.warning(f"删除 Atoms 文件失败: {e}")

            # 删除元数据
            del metadata[video_id]
            self.save_metadata(metadata)

            return {
                "success": True,
                "video_id": video_id,
                "deleted": {
                    "segments_vs_id": segments_vs_id,
                    "segments_file_id": segments_file_id,
                    "atoms_vs_id": atoms_vs_id,
                    "atoms_file_id": atoms_file_id
                }
            }

        except Exception as e:
            logger.error(f"删除双知识库失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def list_all_video_ids(self) -> List[str]:
        """
        列出所有已向量化的视频ID

        Returns:
            视频ID列表
        """
        metadata = self.load_metadata()
        return list(metadata.keys())

    def _count_records(self, jsonl_file: Path) -> int:
        """统计 JSONL 文件记录数"""
        count = 0
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
