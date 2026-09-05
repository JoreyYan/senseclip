# -*- coding: utf-8 -*-
"""
Claude Chat Service - 基于 ChromaDB + Claude Agent 的对话服务
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Import ChromaDB and Claude Agent
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from conversational.chroma_manager import ChromaManager
from conversational.claude_agent import ClaudeAgent

logger = logging.getLogger(__name__)


class ClaudeChatService:
    """Claude Chat 服务 - 管理 ChromaDB + Claude Agent 对话"""

    def __init__(self, data_dir: Path):
        """
        初始化 Claude Chat 服务

        Args:
            data_dir: 数据目录（用于存储 ChromaDB）
        """
        self.data_dir = data_dir
        self.chroma_db_dir = data_dir / "chroma_db"
        self.chroma_db_dir.mkdir(parents=True, exist_ok=True)

        # 获取 API Keys
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")

        # 初始化 ChromaDB Manager
        logger.info(f"初始化 ChromaDB: {self.chroma_db_dir}")
        self.chroma_manager = ChromaManager(
            data_dir=self.chroma_db_dir.parent,
            openai_api_key=self.openai_api_key,
            collection_name="video_multi_level"
        )

        # 初始化 Claude Agent
        logger.info("初始化 Claude Agent")
        self.claude_agent = ClaudeAgent(
            chroma_manager=self.chroma_manager,
            anthropic_api_key=self.anthropic_api_key,
            model="claude-haiku-4-5",
            max_iterations=5
        )

    def is_vectorized(self, video_id: str) -> bool:
        """
        检查视频是否已向量化

        Args:
            video_id: 视频ID

        Returns:
            是否已向量化
        """
        # 检查 ChromaDB 中是否有数据
        try:
            count = self.chroma_manager.collection.count()
            return count > 0
        except:
            return False

    def ensure_vectorized(self, video_id: str) -> bool:
        """
        确保视频已向量化，如果没有则执行向量化

        Args:
            video_id: 视频ID

        Returns:
            是否成功向量化
        """
        if self.is_vectorized(video_id):
            logger.info(f"视频 {video_id} 已向量化")
            return True

        logger.info(f"视频 {video_id} 未向量化，开始向量化...")

        # 读取 multi_level_video.json
        multi_level_file = self.data_dir / "multi_level_video.json"

        if not multi_level_file.exists():
            logger.error(f"找不到 multi_level_video.json: {multi_level_file}")
            return False

        # 导入数据到 ChromaDB
        import json
        records = []
        with open(multi_level_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))

        logger.info(f"找到 {len(records)} 条记录，开始导入...")

        # 转换格式并导入
        chroma_records = []
        for record in records:
            item = {
                "atom_id": record["id"],
                "text": record["text"],
                "level": record["metadata"]["level"],
                "video_id": record["metadata"]["video_id"],
            }

            # 根据级别添加不同字段
            if record["metadata"]["level"] == "atom":
                item["start_ms"] = record["metadata"].get("start_ms", 0)
                item["end_ms"] = record["metadata"].get("end_ms", 0)
                item["duration_ms"] = record["metadata"].get("duration_ms", 0)
            elif record["metadata"]["level"] == "segment":
                item["segment_id"] = record["metadata"].get("segment_id", "")
                item["start_ms"] = record["metadata"].get("start_ms", 0)
                item["end_ms"] = record["metadata"].get("end_ms", 0)
            elif record["metadata"]["level"] == "video":
                item["total_duration_ms"] = record["metadata"].get("total_duration_ms", 0)

            chroma_records.append(item)

        # 批量添加到 ChromaDB
        self.chroma_manager.add_atoms(chroma_records, batch_size=100)
        logger.info(f"成功导入 {len(chroma_records)} 条记录到 ChromaDB")

        return True

    def chat(self, query: str, video_id: Optional[str] = None) -> Dict[str, Any]:
        """
        发送消息到 Claude Agent

        Args:
            query: 用户查询
            video_id: 视频ID（可选，用于过滤特定视频）

        Returns:
            Claude 响应结果
        """
        # 如果指定了 video_id，确保已向量化
        if video_id:
            if not self.ensure_vectorized(video_id):
                return {
                    "success": False,
                    "error": f"视频 {video_id} 未向量化"
                }

        # 调用 Claude Agent
        result = self.claude_agent.run(query)

        # 添加额外信息以供前端展示
        if result.get("success"):
            # 如果是剪辑方案，添加工具调用信息
            if result.get("type") == "cutlist":
                result["tool_calls"] = self._extract_tool_calls(result)

        return result

    def _extract_tool_calls(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从 Claude Agent 结果中提取工具调用信息

        Args:
            result: Claude Agent 响应结果

        Returns:
            工具调用列表
        """
        # 这里简化处理，实际应该从 Claude Agent 的消息历史中提取
        tool_calls = []

        # 假设 Claude 进行了检索和剪辑方案生成
        if result.get("cutlist"):
            cutlist = result["cutlist"]
            tool_calls.append({
                "tool_name": "search_atoms",
                "tool_input": {
                    "query": "相关片段检索",
                    "level": "atom"
                },
                "result": {
                    "count": len(cutlist["atom_ids"])
                }
            })
            tool_calls.append({
                "tool_name": "create_cutlist",
                "tool_input": {
                    "atom_ids": cutlist["atom_ids"],
                    "reasoning": cutlist["reasoning"]
                }
            })

        return tool_calls
