# -*- coding: utf-8 -*-
"""
引用提取与验证器
从 AI 生成的文本中提取 [ATOM_ID @ HH:MM:SS–HH:MM:SS] 格式的引用，并验证其有效性
"""

import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class CitationExtractor:
    """引用提取与验证器"""

    def __init__(self, local_index_manager):
        """
        初始化引用提取器

        Args:
            local_index_manager: LocalIndexManager 实例
        """
        self.local_index_manager = local_index_manager

        # 引用格式正则：[ATOM_ID @ HH:MM:SS–HH:MM:SS]
        # 支持格式：
        # - [42 @ 00:01:23–00:01:45]
        # - [compound_1_5 @ 00:10:30–00:11:00]
        self.citation_pattern = re.compile(
            r'\[(\w+)\s*@\s*(\d{2}:\d{2}:\d{2})\s*–\s*(\d{2}:\d{2}:\d{2})\]'
        )

    def extract_citations(self, text: str) -> List[Dict[str, Any]]:
        """
        从文本中提取所有引用

        Args:
            text: 包含引用的文本

        Returns:
            引用列表，每项包含 atom_id, time_start, time_end, raw_citation
        """
        citations = []
        matches = self.citation_pattern.finditer(text)

        for match in matches:
            atom_id = match.group(1)
            time_start = match.group(2)
            time_end = match.group(3)
            raw_citation = match.group(0)

            citations.append({
                "atom_id": atom_id,
                "time_start": time_start,
                "time_end": time_end,
                "raw_citation": raw_citation,
                "position": {
                    "start": match.start(),
                    "end": match.end()
                }
            })

        logger.info(f"从文本中提取到 {len(citations)} 条引用")
        return citations

    def validate_citations(
        self,
        citations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        验证引用的有效性

        Args:
            citations: 引用列表

        Returns:
            验证结果，包含 valid_citations 和 invalid_citations
        """
        valid_citations = []
        invalid_citations = []

        for citation in citations:
            atom_id = citation["atom_id"]
            time_start = citation["time_start"]
            time_end = citation["time_end"]

            # 从本地索引查找 atom
            atom_info = self.local_index_manager.lookup(atom_id)

            if not atom_info:
                # Atom 不存在
                invalid_citations.append({
                    **citation,
                    "error": "atom_not_found",
                    "message": f"Atom ID {atom_id} 在索引中不存在"
                })
                logger.warning(f"无效引用: Atom ID {atom_id} 不存在")
                continue

            # 验证时间范围
            expected_time_range = atom_info.get("time_range", "")
            if expected_time_range:
                expected_start, expected_end = expected_time_range.split("–")
                if time_start != expected_start or time_end != expected_end:
                    # 时间范围不匹配
                    invalid_citations.append({
                        **citation,
                        "error": "time_mismatch",
                        "message": f"时间范围不匹配：期望 {expected_time_range}，实际 {time_start}–{time_end}",
                        "expected_time_range": expected_time_range,
                        "atom_info": atom_info
                    })
                    logger.warning(f"无效引用: Atom ID {atom_id} 时间范围不匹配")
                    continue

            # 引用有效
            valid_citations.append({
                **citation,
                "atom_info": atom_info,
                "validated": True
            })

        logger.info(f"验证完成: {len(valid_citations)} 条有效, {len(invalid_citations)} 条无效")

        return {
            "valid_citations": valid_citations,
            "invalid_citations": invalid_citations,
            "total": len(citations),
            "valid_count": len(valid_citations),
            "invalid_count": len(invalid_citations)
        }

    def extract_and_validate(self, text: str) -> Dict[str, Any]:
        """
        一站式提取并验证引用

        Args:
            text: 包含引用的文本

        Returns:
            包含提取和验证结果的字典
        """
        citations = self.extract_citations(text)
        validation_result = self.validate_citations(citations)

        return {
            "text": text,
            "citations": citations,
            "validation": validation_result
        }

    def get_citation_statistics(
        self,
        validation_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        获取引用统计信息

        Args:
            validation_result: 验证结果

        Returns:
            统计信息
        """
        valid_citations = validation_result["valid_citations"]
        invalid_citations = validation_result["invalid_citations"]

        # 按错误类型分组
        error_types = {}
        for invalid_citation in invalid_citations:
            error_type = invalid_citation.get("error", "unknown")
            error_types[error_type] = error_types.get(error_type, 0) + 1

        # 统计引用的 atom 分布（按 segment）
        segment_distribution = {}
        for valid_citation in valid_citations:
            atom_info = valid_citation.get("atom_info", {})
            segment_id = atom_info.get("segment_id", "unknown")
            segment_distribution[segment_id] = segment_distribution.get(segment_id, 0) + 1

        return {
            "total_citations": validation_result["total"],
            "valid_count": validation_result["valid_count"],
            "invalid_count": validation_result["invalid_count"],
            "validity_rate": validation_result["valid_count"] / validation_result["total"] if validation_result["total"] > 0 else 0,
            "error_types": error_types,
            "segment_distribution": segment_distribution
        }

    def format_citation_report(
        self,
        validation_result: Dict[str, Any]
    ) -> str:
        """
        生成引用验证报告（文本格式）

        Args:
            validation_result: 验证结果

        Returns:
            格式化的报告文本
        """
        stats = self.get_citation_statistics(validation_result)

        report_lines = [
            "=" * 60,
            "引用验证报告",
            "=" * 60,
            f"总引用数: {stats['total_citations']}",
            f"有效引用: {stats['valid_count']} ({stats['validity_rate']:.1%})",
            f"无效引用: {stats['invalid_count']}",
            ""
        ]

        # 无效引用详情
        if stats["invalid_count"] > 0:
            report_lines.append("无效引用详情:")
            report_lines.append("-" * 60)
            for error_type, count in stats["error_types"].items():
                report_lines.append(f"  {error_type}: {count} 条")

            report_lines.append("")
            for invalid_citation in validation_result["invalid_citations"]:
                report_lines.append(f"  ❌ {invalid_citation['raw_citation']}")
                report_lines.append(f"     错误: {invalid_citation['message']}")
                report_lines.append("")

        # Segment 分布
        if stats["segment_distribution"]:
            report_lines.append("引用分布（按片段）:")
            report_lines.append("-" * 60)
            for segment_id, count in sorted(stats["segment_distribution"].items()):
                report_lines.append(f"  {segment_id}: {count} 条引用")

        report_lines.append("=" * 60)

        return "\n".join(report_lines)

    def repair_invalid_citations(
        self,
        text: str,
        validation_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        尝试修复无效引用（用于 RepairOrchestrator）

        Args:
            text: 原始文本
            validation_result: 验证结果

        Returns:
            修复建议列表
        """
        repair_suggestions = []

        for invalid_citation in validation_result["invalid_citations"]:
            atom_id = invalid_citation["atom_id"]
            error_type = invalid_citation["error"]

            if error_type == "atom_not_found":
                # 尝试模糊匹配（通过文本内容）
                # 提取引用前后的上下文
                position = invalid_citation["position"]
                context_start = max(0, position["start"] - 50)
                context_end = min(len(text), position["end"] + 50)
                context = text[context_start:context_end]

                # 使用 LocalIndexManager 的模糊匹配
                fuzzy_match = self.local_index_manager.fuzzy_match(context, threshold=0.7)

                if fuzzy_match:
                    repair_suggestions.append({
                        "invalid_citation": invalid_citation,
                        "suggested_atom_id": fuzzy_match["atom_id"],
                        "suggested_time_range": fuzzy_match["time_range"],
                        "confidence": fuzzy_match["similarity"],
                        "repair_type": "fuzzy_match"
                    })
                else:
                    repair_suggestions.append({
                        "invalid_citation": invalid_citation,
                        "suggested_action": "remove",
                        "reason": "无法找到匹配的 atom",
                        "repair_type": "remove"
                    })

            elif error_type == "time_mismatch":
                # 时间不匹配，建议使用正确的时间
                atom_info = invalid_citation.get("atom_info", {})
                repair_suggestions.append({
                    "invalid_citation": invalid_citation,
                    "suggested_atom_id": atom_id,
                    "suggested_time_range": atom_info.get("time_range", ""),
                    "repair_type": "time_correction"
                })

        logger.info(f"生成了 {len(repair_suggestions)} 条修复建议")

        return {
            "repair_suggestions": repair_suggestions,
            "repairable_count": len([s for s in repair_suggestions if s["repair_type"] != "remove"]),
            "removable_count": len([s for s in repair_suggestions if s["repair_type"] == "remove"])
        }
