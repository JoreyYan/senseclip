# -*- coding: utf-8 -*-
"""
本地原子索引管理器
用于快速查找、验证和匹配原子（atoms）
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)


class LocalIndexManager:
    """本地原子索引管理"""

    def __init__(self, index_file: Path):
        """
        初始化索引管理器

        Args:
            index_file: local_index.json 文件路径
        """
        self.index_file = index_file
        self.index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self):
        """加载索引文件"""
        if not self.index_file.exists():
            logger.warning(f"索引文件不存在: {self.index_file}")
            return

        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                self.index = json.load(f)
            logger.info(f"成功加载索引: {len(self.index)} 条记录")
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            self.index = {}

    def lookup(self, atom_id: str) -> Optional[Dict[str, Any]]:
        """
        精确查找原子

        Args:
            atom_id: 原子ID（字符串格式，如 "1" 或 "compound_42_1"）

        Returns:
            原子信息字典，如果不存在则返回 None
        """
        # 支持整数 ID（转为字符串）
        atom_id_str = str(atom_id)
        return self.index.get(atom_id_str)

    def fuzzy_match(self, quote: str, threshold: float = 0.8) -> Optional[Dict[str, Any]]:
        """
        模糊匹配（用于从引用文本反查 atom）

        Args:
            quote: 引用文本片段
            threshold: 相似度阈值（0-1）

        Returns:
            最佳匹配的原子信息，如果没有匹配则返回 None
        """
        if not quote or not self.index:
            return None

        best_match = None
        best_score = 0.0

        for atom_id, atom_info in self.index.items():
            content = atom_info.get("content", "")
            if not content:
                continue

            # 计算相似度
            similarity = SequenceMatcher(None, quote.lower(), content.lower()).ratio()

            if similarity > best_score and similarity >= threshold:
                best_score = similarity
                best_match = {
                    "atom_id": atom_id,
                    "similarity": similarity,
                    **atom_info
                }

        if best_match:
            logger.debug(f"模糊匹配成功: atom_id={best_match['atom_id']}, 相似度={best_score:.2f}")

        return best_match

    def validate_atom_exists(self, atom_id: str) -> bool:
        """
        验证原子是否存在

        Args:
            atom_id: 原子ID

        Returns:
            是否存在
        """
        return str(atom_id) in self.index

    def get_atoms_in_segment(self, segment_id: str) -> List[Dict[str, Any]]:
        """
        获取某个 segment 下的所有 atoms

        Args:
            segment_id: 片段ID（如 "seg_1"）

        Returns:
            原子信息列表（包含 atom_id 字段）
        """
        segment_id_str = str(segment_id)
        result = []

        for atom_id, atom_info in self.index.items():
            if atom_info.get("segment_id") == segment_id_str:
                result.append({
                    "atom_id": atom_id,
                    **atom_info
                })

        # 按时间排序
        result.sort(key=lambda x: x.get("start_ms", 0))
        return result

    def get_atoms_in_time_range(
        self,
        start_ms: int,
        end_ms: int,
        video_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取指定时间范围内的所有原子

        Args:
            start_ms: 开始时间（毫秒）
            end_ms: 结束时间（毫秒）
            video_id: 可选的视频ID过滤

        Returns:
            原子信息列表
        """
        result = []

        for atom_id, atom_info in self.index.items():
            # 视频ID过滤
            if video_id and atom_info.get("video_id") != video_id:
                continue

            atom_start = atom_info.get("start_ms", 0)
            atom_end = atom_info.get("end_ms", 0)

            # 检查时间重叠
            if atom_start < end_ms and atom_end > start_ms:
                result.append({
                    "atom_id": atom_id,
                    **atom_info
                })

        # 按时间排序
        result.sort(key=lambda x: x.get("start_ms", 0))
        return result

    def get_atoms_by_emotion(self, emotion: str) -> List[Dict[str, Any]]:
        """
        获取指定情感类型的所有原子

        Args:
            emotion: 情感类型（如 "joy", "anger", "neutral"）

        Returns:
            原子信息列表
        """
        result = []

        for atom_id, atom_info in self.index.items():
            if atom_info.get("emotion") == emotion:
                result.append({
                    "atom_id": atom_id,
                    **atom_info
                })

        # 按重要性排序
        result.sort(key=lambda x: x.get("importance", 0), reverse=True)
        return result

    def get_atoms_by_importance(
        self,
        min_importance: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        获取重要性高于阈值的原子

        Args:
            min_importance: 最低重要性分数

        Returns:
            原子信息列表（按重要性降序）
        """
        result = []

        for atom_id, atom_info in self.index.items():
            importance = atom_info.get("importance", 0.0)
            if importance >= min_importance:
                result.append({
                    "atom_id": atom_id,
                    **atom_info
                })

        # 按重要性降序排序
        result.sort(key=lambda x: x.get("importance", 0), reverse=True)
        return result

    def search_by_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        """
        按关键词搜索原子内容

        Args:
            keyword: 关键词

        Returns:
            匹配的原子信息列表
        """
        result = []
        keyword_lower = keyword.lower()

        for atom_id, atom_info in self.index.items():
            content = atom_info.get("content", "").lower()
            if keyword_lower in content:
                result.append({
                    "atom_id": atom_id,
                    **atom_info
                })

        return result

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取索引统计信息

        Returns:
            统计信息字典
        """
        if not self.index:
            return {
                "total_atoms": 0,
                "total_duration_seconds": 0,
                "avg_duration_seconds": 0,
                "emotion_distribution": {},
                "importance_distribution": {}
            }

        total_atoms = len(self.index)
        total_duration = sum(
            atom_info.get("duration_seconds", 0)
            for atom_info in self.index.values()
        )

        # 情感分布
        emotion_dist = {}
        for atom_info in self.index.values():
            emotion = atom_info.get("emotion", "neutral")
            emotion_dist[emotion] = emotion_dist.get(emotion, 0) + 1

        # 重要性分布
        importance_ranges = {
            "high (>0.7)": 0,
            "medium (0.4-0.7)": 0,
            "low (<0.4)": 0
        }
        for atom_info in self.index.values():
            importance = atom_info.get("importance", 0.5)
            if importance > 0.7:
                importance_ranges["high (>0.7)"] += 1
            elif importance >= 0.4:
                importance_ranges["medium (0.4-0.7)"] += 1
            else:
                importance_ranges["low (<0.4)"] += 1

        return {
            "total_atoms": total_atoms,
            "total_duration_seconds": total_duration,
            "avg_duration_seconds": total_duration / total_atoms if total_atoms > 0 else 0,
            "emotion_distribution": emotion_dist,
            "importance_distribution": importance_ranges
        }
