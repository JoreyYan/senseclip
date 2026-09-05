"""
SegmentIdentifier - 叙事片段识别器
识别视频中的完整叙事单元（3-15分钟）
"""

import json
import re
from typing import List
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Atom, SegmentMeta
from utils import ClaudeClient, setup_logger

logger = setup_logger(__name__)


class SegmentIdentifier:
    """叙事片段识别器"""

    def __init__(self, api_key: str):
        self.client = ClaudeClient(api_key)

        # 加载提示词
        prompt_path = Path(__file__).parent.parent / 'prompts' / 'identify_segments.txt'
        if prompt_path.exists():
            with open(prompt_path, 'r', encoding='utf-8') as f:
                self.prompt_template = f.read()
        else:
            # 如果提示词文件不存在，使用内嵌的简化版
            self.prompt_template = self._get_default_prompt()

    def identify_segments(self, atoms: List[Atom]) -> List[SegmentMeta]:
        """
        识别叙事片段

        Args:
            atoms: 原子列表

        Returns:
            片段元数据列表
        """
        logger.info(f"开始识别叙事片段，输入{len(atoms)}个原子")

        # Step 1: 规则筛选 - 找出潜在的重要片段
        candidates = self._rule_based_filter(atoms)
        logger.info(f"规则筛选：找到{len(candidates)}个候选片段")

        # Step 2: AI精炼 - 验证和优化片段边界
        if len(candidates) > 0:
            segments = self._ai_refine(candidates, atoms)
            logger.info(f"AI精炼：最终确定{len(segments)}个叙事片段")
        else:
            # 如果没有候选，创建整体片段
            logger.warning("未找到候选片段，将整个视频作为单一片段")
            segments = self._create_default_segment(atoms)

        return segments

    def _atom_field(self, atom, key: str, default=None):
        """Read atom field from either model object or dict."""
        if isinstance(atom, dict):
            return atom.get(key, default)
        return getattr(atom, key, default)

    def _rule_based_filter(self, atoms) -> List[SegmentMeta]:
        """
        基于规则的初步筛选

        筛选条件：
        1. 完整性 = "完整"（排除需要上下文的碎片）
        2. 时长 >= 15秒（排除过短的内容）
        """
        # 首先筛选出符合条件的原子
        qualified_atoms = []
        for atom in atoms:
            # 兼容 atom 既可能是 Pydantic 模型，也可能是 dict
            completeness = atom.get("completeness") if isinstance(atom, dict) else getattr(atom, "completeness", None)
            duration_seconds = atom.get("duration_seconds") if isinstance(atom, dict) else getattr(atom, "duration_seconds", 0)

            # 检查完整性
            is_complete = completeness == "完整"

            # 检查时长（降低到15秒，因为原子级别本来就是10-20秒）
            is_long_enough = (duration_seconds or 0) >= 15

            if is_complete and is_long_enough:
                qualified_atoms.append(atom)

        logger.info(f"  符合条件的原子：{len(qualified_atoms)}/{len(atoms)}个")

        # 将连续的合格原子聚合成候选片段
        candidates = []
        current_group = []

        for i, atom in enumerate(atoms):
            if atom in qualified_atoms:
                current_group.append(atom)
            else:
                # 遇到不合格原子，如果当前组足够大，则保存
                if len(current_group) >= 3:  # 至少3个原子
                    candidates.append(self._create_segment_meta(current_group, len(candidates) + 1))
                current_group = []

        # 处理最后一组
        if len(current_group) >= 3:
            candidates.append(self._create_segment_meta(current_group, len(candidates) + 1))

        # 合并过短的候选片段（< 3分钟）
        candidates = self._merge_short_segments(candidates, atoms, min_duration_ms=180000)

        return candidates

    def _create_segment_meta(self, atoms: List[Atom], segment_num: int) -> SegmentMeta:
        """创建片段元数据"""
        atom_ids = [self._atom_field(atom, "atom_id") for atom in atoms]
        start_ms = min(self._atom_field(atom, "start_ms", 0) for atom in atoms)
        end_ms = max(self._atom_field(atom, "end_ms", 0) for atom in atoms)
        duration_ms = end_ms - start_ms

        return SegmentMeta(
            segment_num=segment_num,
            atoms=atom_ids,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=duration_ms,
            reason="规则筛选：连续的完整陈述片段",
            confidence=0.8
        )

    def _merge_short_segments(
        self,
        candidates: List[SegmentMeta],
        atoms: List[Atom],
        min_duration_ms: int = 180000  # 3分钟
    ) -> List[SegmentMeta]:
        """
        合并过短的片段

        如果相邻片段都过短（< 3分钟），则合并
        """
        if len(candidates) <= 1:
            return candidates

        merged = []
        i = 0

        while i < len(candidates):
            current = candidates[i]

            # 如果当前片段过短，尝试与下一个合并
            if current.duration_ms < min_duration_ms and i + 1 < len(candidates):
                next_seg = candidates[i + 1]

                # 合并
                merged_atoms = current.atoms + next_seg.atoms
                merged_segment = SegmentMeta(
                    segment_num=len(merged) + 1,
                    atoms=merged_atoms,
                    start_ms=current.start_ms,
                    end_ms=next_seg.end_ms,
                    duration_ms=next_seg.end_ms - current.start_ms,
                    reason="合并过短片段",
                    confidence=0.7
                )
                merged.append(merged_segment)
                i += 2  # 跳过下一个
            else:
                # 重新编号
                current.segment_num = len(merged) + 1
                merged.append(current)
                i += 1

        logger.info(f"  合并后：{len(merged)}个片段")
        return merged

    def _ai_refine(self, candidates: List[SegmentMeta], atoms: List[Atom]) -> List[SegmentMeta]:
        """
        AI精炼片段边界

        使用Claude分析候选片段，确认是否合理，并优化边界
        """
        # 构建atoms的索引
        atoms_dict = {
            self._atom_field(atom, "atom_id"): atom
            for atom in atoms
            if self._atom_field(atom, "atom_id")
        }

        # 构建输入数据
        candidates_data = []
        for seg in candidates:
            seg_atoms = [atoms_dict[aid] for aid in seg.atoms if aid in atoms_dict]
            if len(seg_atoms) == 0:
                continue

            # 提取关键信息
            candidates_data.append({
                "segment_num": seg.segment_num,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "duration_minutes": round(seg.duration_minutes, 1),
                "atom_count": len(seg.atoms),
                "preview_text": (
                    (self._atom_field(seg_atoms[0], "merged_text", "") or "")[:100] + "..."
                    if len(seg_atoms) > 0 else ""
                )
            })

        # 构建提示词
        input_text = json.dumps(candidates_data, ensure_ascii=False, indent=2)
        prompt = self.prompt_template.replace("{CANDIDATES}", input_text)

        try:
            # 调用API
            response = self.client.call(prompt, max_tokens=2000)

            # 解析响应
            refined_segments = self._parse_ai_response(response, candidates)

            return refined_segments

        except Exception as e:
            logger.error(f"AI精炼失败: {e}")
            logger.warning("回退到规则识别结果")
            return candidates

    def _parse_ai_response(self, response: str, original_candidates: List[SegmentMeta]) -> List[SegmentMeta]:
        """解析AI响应"""
        try:
            # 提取JSON数组
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                logger.warning("AI响应中未找到JSON，使用原始候选")
                return original_candidates

            json_str = json_match.group(0)
            refined_data = json.loads(json_str)

            # 将AI的结果与原始候选对应
            refined_segments = []
            for item in refined_data:
                seg_num = item.get('segment_num', 0)
                keep = item.get('keep', True)

                if not keep:
                    logger.info(f"  AI建议移除片段{seg_num}")
                    continue

                # 找到对应的原始候选
                original = next((s for s in original_candidates if s.segment_num == seg_num), None)
                if original:
                    # 更新置信度和原因
                    original.confidence = item.get('confidence', original.confidence)
                    original.reason = item.get('reason', original.reason)
                    refined_segments.append(original)

            # 重新编号
            for i, seg in enumerate(refined_segments):
                seg.segment_num = i + 1

            return refined_segments

        except Exception as e:
            logger.error(f"解析AI响应失败: {e}")
            return original_candidates

    def _create_default_segment(self, atoms: List[Atom]) -> List[SegmentMeta]:
        """创建默认片段（整个视频作为一个片段）"""
        if len(atoms) == 0:
            return []

        atom_ids = [self._atom_field(atom, "atom_id") for atom in atoms]
        start_ms = min(self._atom_field(atom, "start_ms", 0) for atom in atoms)
        end_ms = max(self._atom_field(atom, "end_ms", 0) for atom in atoms)

        segment = SegmentMeta(
            segment_num=1,
            atoms=atom_ids,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            reason="默认：整个视频作为单一片段",
            confidence=0.5
        )

        return [segment]

    def _get_default_prompt(self) -> str:
        """获取默认提示词（如果文件不存在）"""
        return """你是一个视频内容结构分析专家。我给你一个候选叙事片段列表，请判断这些片段是否合理。

【候选片段】
{CANDIDATES}

【任务】
对于每个候选片段，判断：
1. 是否是一个完整的叙事单元（有开头、发展、结尾）
2. 是否适合作为独立的内容片段使用
3. 时长是否合理（3-15分钟为佳）

【输出格式】
返回JSON数组，每个元素包含：
- segment_num: 片段序号
- keep: 是否保留（true/false）
- confidence: 置信度（0-1）
- reason: 判断理由（简短说明）

示例：
[
  {"segment_num": 1, "keep": true, "confidence": 0.9, "reason": "完整的历史叙事，从背景到结果"},
  {"segment_num": 2, "keep": false, "confidence": 0.6, "reason": "时长过短且内容碎片化"}
]

【输出】"""
