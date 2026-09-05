# -*- coding: utf-8 -*-
"""
结构验证器 - 验证 AI 输出的结构完整性
确保输出总是可用的（有引用、有时间戳、有内容）
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class StructureValidator:
    """结构验证器"""

    def __init__(self, citation_extractor):
        """
        初始化结构验证器

        Args:
            citation_extractor: CitationExtractor 实例
        """
        self.citation_extractor = citation_extractor

    def validate_output_structure(
        self,
        output_text: str,
        min_citations: int = 1,
        require_timestamps: bool = True
    ) -> Dict[str, Any]:
        """
        验证输出结构的完整性

        Args:
            output_text: AI 生成的输出文本
            min_citations: 最少引用数量
            require_timestamps: 是否要求引用包含时间戳

        Returns:
            验证结果
        """
        issues = []
        warnings = []

        # 1. 检查是否为空
        if not output_text or not output_text.strip():
            issues.append({
                "type": "empty_output",
                "severity": "critical",
                "message": "输出为空"
            })
            return {
                "valid": False,
                "issues": issues,
                "warnings": warnings,
                "score": 0.0
            }

        # 2. 提取并验证引用
        citation_result = self.citation_extractor.extract_and_validate(output_text)
        citations = citation_result["citations"]
        validation = citation_result["validation"]

        # 3. 检查引用数量
        if len(citations) < min_citations:
            issues.append({
                "type": "insufficient_citations",
                "severity": "high",
                "message": f"引用数量不足：需要至少 {min_citations} 条，实际 {len(citations)} 条"
            })

        # 4. 检查无效引用
        if validation["invalid_count"] > 0:
            issues.append({
                "type": "invalid_citations",
                "severity": "high",
                "message": f"包含 {validation['invalid_count']} 条无效引用",
                "details": validation["invalid_citations"]
            })

        # 5. 检查时间戳（如果要求）
        if require_timestamps:
            for citation in citations:
                if not citation.get("time_start") or not citation.get("time_end"):
                    issues.append({
                        "type": "missing_timestamp",
                        "severity": "medium",
                        "message": f"引用缺少时间戳: {citation['raw_citation']}"
                    })

        # 6. 检查内容质量
        content_issues = self._validate_content_quality(output_text, citations)
        issues.extend(content_issues)

        # 7. 检查格式规范
        format_warnings = self._validate_format(output_text)
        warnings.extend(format_warnings)

        # 8. 计算综合评分
        score = self._calculate_structure_score(
            output_text,
            citations,
            validation,
            issues,
            warnings
        )

        # 9. 判断是否通过验证
        critical_issues = [i for i in issues if i["severity"] == "critical"]
        high_issues = [i for i in issues if i["severity"] == "high"]

        valid = len(critical_issues) == 0 and len(high_issues) == 0

        return {
            "valid": valid,
            "score": score,
            "issues": issues,
            "warnings": warnings,
            "citation_validation": validation,
            "statistics": {
                "total_citations": len(citations),
                "valid_citations": validation["valid_count"],
                "invalid_citations": validation["invalid_count"],
                "critical_issues": len(critical_issues),
                "high_issues": len(high_issues),
                "warnings": len(warnings)
            }
        }

    def _validate_content_quality(
        self,
        output_text: str,
        citations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """验证内容质量"""
        issues = []

        # 1. 检查内容长度
        if len(output_text) < 50:
            issues.append({
                "type": "content_too_short",
                "severity": "medium",
                "message": f"内容过短: {len(output_text)} 字符"
            })

        # 2. 检查引用分布
        if citations:
            # 计算文本中引用的分布
            text_length = len(output_text)
            citation_positions = [c["position"]["start"] for c in citations]

            # 检查是否所有引用都集中在文本的某一部分
            first_quarter = text_length * 0.25
            last_quarter = text_length * 0.75

            citations_in_first_quarter = sum(1 for pos in citation_positions if pos < first_quarter)
            citations_in_last_quarter = sum(1 for pos in citation_positions if pos > last_quarter)

            if citations_in_first_quarter == len(citations):
                issues.append({
                    "type": "citations_concentrated",
                    "severity": "low",
                    "message": "所有引用集中在文本前部"
                })
            elif citations_in_last_quarter == len(citations):
                issues.append({
                    "type": "citations_concentrated",
                    "severity": "low",
                    "message": "所有引用集中在文本后部"
                })

        # 3. 检查是否有实质性内容（不只是引用）
        text_without_citations = output_text
        for citation in citations:
            text_without_citations = text_without_citations.replace(
                citation["raw_citation"], ""
            )

        # 去除空白后检查
        content_only = text_without_citations.strip()
        if len(content_only) < 30:
            issues.append({
                "type": "insufficient_content",
                "severity": "medium",
                "message": "除引用外实质性内容不足"
            })

        return issues

    def _validate_format(self, output_text: str) -> List[Dict[str, Any]]:
        """验证格式规范"""
        warnings = []

        # 1. 检查是否有明确的段落结构
        paragraphs = [p for p in output_text.split("\n\n") if p.strip()]
        if len(paragraphs) == 1 and len(output_text) > 500:
            warnings.append({
                "type": "missing_paragraphs",
                "message": "建议将长文本分段以提高可读性"
            })

        # 2. 检查是否有过长的句子（可能影响可读性）
        sentences = output_text.split("。")
        long_sentences = [s for s in sentences if len(s) > 200]
        if long_sentences:
            warnings.append({
                "type": "long_sentences",
                "message": f"包含 {len(long_sentences)} 个过长的句子"
            })

        return warnings

    def _calculate_structure_score(
        self,
        output_text: str,
        citations: List[Dict[str, Any]],
        validation: Dict[str, Any],
        issues: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]]
    ) -> float:
        """
        计算结构评分（0-1）

        评分标准:
        - 基础分: 50分
        - 引用质量: 30分（有效引用比例）
        - 内容质量: 20分（长度、分布等）
        - 扣分: 每个 critical 问题扣20分，high 扣10分，medium 扣5分
        """
        score = 50.0

        # 引用质量分数（30分）
        if validation["total"] > 0:
            citation_score = 30.0 * (validation["valid_count"] / validation["total"])
        else:
            citation_score = 0.0
        score += citation_score

        # 内容质量分数（20分）
        content_score = 20.0
        if len(output_text) < 50:
            content_score -= 10.0
        if len(output_text) < 20:
            content_score -= 10.0
        score += max(0, content_score)

        # 扣分
        for issue in issues:
            severity = issue["severity"]
            if severity == "critical":
                score -= 20.0
            elif severity == "high":
                score -= 10.0
            elif severity == "medium":
                score -= 5.0
            elif severity == "low":
                score -= 2.0

        # 归一化到 0-1
        final_score = max(0.0, min(1.0, score / 100.0))

        return final_score

    def generate_validation_report(
        self,
        validation_result: Dict[str, Any]
    ) -> str:
        """
        生成验证报告（文本格式）

        Args:
            validation_result: 验证结果

        Returns:
            格式化的报告文本
        """
        report_lines = [
            "=" * 60,
            "结构验证报告",
            "=" * 60,
            f"验证状态: {'✅ 通过' if validation_result['valid'] else '❌ 未通过'}",
            f"综合评分: {validation_result['score']:.2%}",
            ""
        ]

        # 统计信息
        stats = validation_result["statistics"]
        report_lines.append("统计信息:")
        report_lines.append("-" * 60)
        report_lines.append(f"  总引用数: {stats['total_citations']}")
        report_lines.append(f"  有效引用: {stats['valid_citations']}")
        report_lines.append(f"  无效引用: {stats['invalid_citations']}")
        report_lines.append(f"  严重问题: {stats['critical_issues']}")
        report_lines.append(f"  高级问题: {stats['high_issues']}")
        report_lines.append(f"  警告: {stats['warnings']}")
        report_lines.append("")

        # 问题详情
        if validation_result["issues"]:
            report_lines.append("问题详情:")
            report_lines.append("-" * 60)
            for issue in validation_result["issues"]:
                severity_icon = {
                    "critical": "🔴",
                    "high": "🟠",
                    "medium": "🟡",
                    "low": "🟢"
                }.get(issue["severity"], "⚪")
                report_lines.append(f"  {severity_icon} [{issue['severity'].upper()}] {issue['message']}")
            report_lines.append("")

        # 警告
        if validation_result["warnings"]:
            report_lines.append("警告:")
            report_lines.append("-" * 60)
            for warning in validation_result["warnings"]:
                report_lines.append(f"  ⚠️  {warning['message']}")
            report_lines.append("")

        report_lines.append("=" * 60)

        return "\n".join(report_lines)

    def is_repairable(self, validation_result: Dict[str, Any]) -> bool:
        """
        判断输出是否可修复

        Args:
            validation_result: 验证结果

        Returns:
            是否可修复
        """
        # 如果有 critical 问题，不可修复
        critical_issues = [
            i for i in validation_result["issues"]
            if i["severity"] == "critical"
        ]

        if critical_issues:
            return False

        # 如果无效引用数量过多（超过50%），不可修复
        citation_validation = validation_result["citation_validation"]
        if citation_validation["total"] > 0:
            invalid_rate = citation_validation["invalid_count"] / citation_validation["total"]
            if invalid_rate > 0.5:
                return False

        return True
