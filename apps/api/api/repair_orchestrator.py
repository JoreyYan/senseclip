# -*- coding: utf-8 -*-
"""
自动修复编排器 - 当输出结构不合格时自动修复
"""

import logging
from typing import Dict, Any, Optional
import re

logger = logging.getLogger(__name__)


class RepairOrchestrator:
    """自动修复编排器"""

    def __init__(
        self,
        citation_extractor,
        structure_validator,
        two_phase_generator
    ):
        """
        初始化修复编排器

        Args:
            citation_extractor: CitationExtractor 实例
            structure_validator: StructureValidator 实例
            two_phase_generator: TwoPhaseGenerator 实例
        """
        self.citation_extractor = citation_extractor
        self.structure_validator = structure_validator
        self.two_phase_generator = two_phase_generator

    def repair_output(
        self,
        output_text: str,
        validation_result: Dict[str, Any],
        video_id: str,
        original_query: str,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
        """
        尝试修复不合格的输出

        Args:
            output_text: 原始输出文本
            validation_result: 验证结果
            video_id: 视频ID
            original_query: 原始查询
            max_attempts: 最大重试次数

        Returns:
            修复结果
        """
        logger.info("开始修复输出...")

        # 1. 判断是否可修复
        if not self.structure_validator.is_repairable(validation_result):
            logger.warning("输出不可修复")
            return {
                "success": False,
                "error": "输出存在严重问题，无法修复",
                "original_output": output_text,
                "validation_result": validation_result
            }

        # 2. 分析问题类型并选择修复策略
        repair_strategies = self._determine_repair_strategies(validation_result)

        logger.info(f"确定修复策略: {[s['strategy'] for s in repair_strategies]}")

        # 3. 执行修复
        repaired_text = output_text
        repair_history = []

        for strategy_info in repair_strategies:
            strategy = strategy_info["strategy"]
            details = strategy_info.get("details", {})

            logger.info(f"执行修复策略: {strategy}")

            if strategy == "fix_invalid_citations":
                result = self._fix_invalid_citations(repaired_text, details)
            elif strategy == "add_missing_citations":
                result = self._add_missing_citations(
                    repaired_text, details, video_id, original_query
                )
            elif strategy == "regenerate":
                result = self._regenerate_output(video_id, original_query, max_attempts)
            else:
                logger.warning(f"未知修复策略: {strategy}")
                continue

            if result["success"]:
                repaired_text = result["repaired_text"]
                repair_history.append({
                    "strategy": strategy,
                    "success": True,
                    "changes": result.get("changes", "")
                })
            else:
                repair_history.append({
                    "strategy": strategy,
                    "success": False,
                    "error": result.get("error", "")
                })

        # 4. 重新验证修复后的输出
        logger.info("重新验证修复后的输出...")
        final_validation = self.structure_validator.validate_output_structure(repaired_text)

        return {
            "success": final_validation["valid"],
            "original_output": output_text,
            "repaired_output": repaired_text,
            "original_validation": validation_result,
            "final_validation": final_validation,
            "repair_history": repair_history,
            "improvement": {
                "score_before": validation_result["score"],
                "score_after": final_validation["score"],
                "score_delta": final_validation["score"] - validation_result["score"]
            }
        }

    def _determine_repair_strategies(
        self,
        validation_result: Dict[str, Any]
    ) -> list:
        """
        根据验证结果确定修复策略

        Args:
            validation_result: 验证结果

        Returns:
            修复策略列表（按优先级排序）
        """
        strategies = []
        issues = validation_result["issues"]

        # 检查无效引用问题
        invalid_citation_issues = [
            i for i in issues
            if i["type"] == "invalid_citations"
        ]
        if invalid_citation_issues:
            strategies.append({
                "strategy": "fix_invalid_citations",
                "details": invalid_citation_issues[0]
            })

        # 检查引用数量不足
        insufficient_citation_issues = [
            i for i in issues
            if i["type"] == "insufficient_citations"
        ]
        if insufficient_citation_issues:
            strategies.append({
                "strategy": "add_missing_citations",
                "details": insufficient_citation_issues[0]
            })

        # 如果问题太多，直接重新生成
        if len(issues) >= 5:
            strategies = [{
                "strategy": "regenerate",
                "details": {"reason": "问题过多"}
            }]

        return strategies

    def _fix_invalid_citations(
        self,
        text: str,
        details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        修复无效引用

        Args:
            text: 原始文本
            details: 问题详情

        Returns:
            修复结果
        """
        logger.info("修复无效引用...")

        invalid_citations = details.get("details", [])
        if not invalid_citations:
            return {
                "success": False,
                "error": "没有找到无效引用详情"
            }

        # 获取修复建议
        validation_result = self.citation_extractor.extract_and_validate(text)
        repair_suggestions = self.citation_extractor.repair_invalid_citations(
            text, validation_result
        )

        repaired_text = text
        changes = []

        for suggestion in repair_suggestions["repair_suggestions"]:
            invalid_citation = suggestion["invalid_citation"]
            repair_type = suggestion["repair_type"]

            if repair_type == "remove":
                # 移除无效引用
                repaired_text = repaired_text.replace(
                    invalid_citation["raw_citation"], ""
                )
                changes.append(f"移除无效引用: {invalid_citation['raw_citation']}")

            elif repair_type == "time_correction":
                # 修正时间范围
                old_citation = invalid_citation["raw_citation"]
                atom_id = suggestion["suggested_atom_id"]
                new_time_range = suggestion["suggested_time_range"]
                time_start, time_end = new_time_range.split("–")

                new_citation = f"[{atom_id} @ {time_start}–{time_end}]"
                repaired_text = repaired_text.replace(old_citation, new_citation)
                changes.append(f"修正时间: {old_citation} → {new_citation}")

            elif repair_type == "fuzzy_match":
                # 使用模糊匹配的结果替换
                old_citation = invalid_citation["raw_citation"]
                atom_id = suggestion["suggested_atom_id"]
                new_time_range = suggestion["suggested_time_range"]
                time_start, time_end = new_time_range.split("–")

                new_citation = f"[{atom_id} @ {time_start}–{time_end}]"
                repaired_text = repaired_text.replace(old_citation, new_citation)
                changes.append(f"模糊匹配替换: {old_citation} → {new_citation}")

        logger.info(f"修复了 {len(changes)} 处无效引用")

        return {
            "success": True,
            "repaired_text": repaired_text,
            "changes": "\n".join(changes)
        }

    def _add_missing_citations(
        self,
        text: str,
        details: Dict[str, Any],
        video_id: str,
        original_query: str
    ) -> Dict[str, Any]:
        """
        添加缺失的引用

        注意: 这个方法相对复杂，需要重新调用 AI 来补充引用
        当前实现为简化版本，返回建议用户重新生成

        Args:
            text: 原始文本
            details: 问题详情
            video_id: 视频ID
            original_query: 原始查询

        Returns:
            修复结果
        """
        logger.info("检测到引用数量不足，建议重新生成...")

        # 当前简化实现：返回失败，建议重新生成
        return {
            "success": False,
            "error": "引用数量不足，建议使用 regenerate 策略"
        }

    def _regenerate_output(
        self,
        video_id: str,
        original_query: str,
        max_attempts: int
    ) -> Dict[str, Any]:
        """
        重新生成输出

        Args:
            video_id: 视频ID
            original_query: 原始查询
            max_attempts: 最大重试次数

        Returns:
            重新生成结果
        """
        logger.info(f"重新生成输出 (最多重试 {max_attempts} 次)...")

        for attempt in range(max_attempts):
            logger.info(f"第 {attempt + 1} 次尝试...")

            # 调用 TwoPhaseGenerator 重新生成
            result = self.two_phase_generator.generate_two_phase(
                video_id=video_id,
                query=original_query
            )

            if not result["success"]:
                logger.warning(f"第 {attempt + 1} 次生成失败: {result.get('error', '')}")
                continue

            # 验证新输出
            new_text = result["final_answer"]
            validation = self.structure_validator.validate_output_structure(new_text)

            if validation["valid"]:
                logger.info(f"第 {attempt + 1} 次生成成功，输出验证通过")
                return {
                    "success": True,
                    "repaired_text": new_text,
                    "changes": f"重新生成成功（第 {attempt + 1} 次尝试）"
                }
            else:
                logger.warning(f"第 {attempt + 1} 次生成的输出验证未通过")

        # 所有重试都失败
        logger.error("重新生成失败，已达到最大重试次数")
        return {
            "success": False,
            "error": f"重新生成失败，已尝试 {max_attempts} 次"
        }

    def auto_repair_pipeline(
        self,
        video_id: str,
        query: str,
        model: str = "gpt-4o",
        max_repair_attempts: int = 2
    ) -> Dict[str, Any]:
        """
        自动修复流水线：生成 → 验证 → 修复（如需要）

        Args:
            video_id: 视频ID
            query: 用户查询
            model: 使用的模型
            max_repair_attempts: 最大修复尝试次数

        Returns:
            最终结果
        """
        logger.info("启动自动修复流水线...")

        # 1. 生成初始输出
        logger.info("Step 1: 生成初始输出...")
        generation_result = self.two_phase_generator.generate_two_phase(
            video_id=video_id,
            query=query,
            model=model
        )

        if not generation_result["success"]:
            return {
                "success": False,
                "error": f"初始生成失败: {generation_result.get('error', '')}"
            }

        initial_output = generation_result["final_answer"]

        # 2. 验证输出
        logger.info("Step 2: 验证输出结构...")
        validation_result = self.structure_validator.validate_output_structure(initial_output)

        if validation_result["valid"]:
            logger.info("✅ 输出验证通过，无需修复")
            return {
                "success": True,
                "output": initial_output,
                "validation": validation_result,
                "repaired": False,
                "generation_result": generation_result
            }

        # 3. 输出不合格，尝试修复
        logger.info("⚠️  输出验证未通过，启动修复流程...")
        repair_result = self.repair_output(
            output_text=initial_output,
            validation_result=validation_result,
            video_id=video_id,
            original_query=query,
            max_attempts=max_repair_attempts
        )

        if repair_result["success"]:
            logger.info("✅ 修复成功")
            return {
                "success": True,
                "output": repair_result["repaired_output"],
                "validation": repair_result["final_validation"],
                "repaired": True,
                "repair_result": repair_result,
                "generation_result": generation_result
            }
        else:
            logger.error("❌ 修复失败")
            return {
                "success": False,
                "error": "输出验证失败且无法修复",
                "output": initial_output,
                "validation": validation_result,
                "repair_result": repair_result,
                "generation_result": generation_result
            }
