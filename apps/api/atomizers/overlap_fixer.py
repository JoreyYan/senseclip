"""
时间重叠修复器
使用代码逻辑修正LLM生成的原子之间的时间重叠问题
"""

from typing import List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Atom
from utils import setup_logger

logger = setup_logger(__name__)


class OverlapFixer:
    """时间重叠修复器"""

    def __init__(self, strategy: str = 'adjust_boundary'):
        """
        初始化修复器

        Args:
            strategy: 修复策略
                - 'adjust_boundary': 调整边界（默认，保守策略）
                - 'proportional_split': 按比例分割重叠区域
        """
        self.strategy = strategy

    def fix(self, atoms: List[Atom]) -> List[Atom]:
        """
        修复原子列表中的时间重叠

        Args:
            atoms: 原子列表（按时间排序）

        Returns:
            修复后的原子列表
        """
        if len(atoms) <= 1:
            return atoms

        # 按start_ms排序（确保顺序正确）
        atoms_sorted = sorted(atoms, key=lambda a: a.start_ms)

        fixed_atoms = []
        fixes_count = 0

        for i, atom in enumerate(atoms_sorted):
            if i == 0:
                # 第一个原子，直接添加
                fixed_atoms.append(atom)
                continue

            prev_atom = fixed_atoms[-1]

            # 检查是否有重叠
            if atom.start_ms < prev_atom.end_ms:
                fixes_count += 1
                overlap_ms = prev_atom.end_ms - atom.start_ms

                logger.debug(f"  检测到重叠: {prev_atom.atom_id} 和 {atom.atom_id}, 重叠{overlap_ms}ms")

                if self.strategy == 'adjust_boundary':
                    # 策略1: 调整边界（将当前原子的start_ms设为前一个的end_ms）
                    fixed_atom = self._adjust_boundary(atom, prev_atom)
                elif self.strategy == 'proportional_split':
                    # 策略2: 按比例分割（修改前一个的end_ms和当前的start_ms）
                    fixed_prev, fixed_atom = self._proportional_split(prev_atom, atom)
                    # 更新已添加的前一个原子
                    fixed_atoms[-1] = fixed_prev
                else:
                    fixed_atom = atom

                fixed_atoms.append(fixed_atom)
            else:
                # 无重叠，直接添加
                fixed_atoms.append(atom)

        if fixes_count > 0:
            logger.info(f"修复了 {fixes_count} 处时间重叠")

        return fixed_atoms

    def _adjust_boundary(self, atom: Atom, prev_atom: Atom) -> Atom:
        """
        策略1: 调整边界
        将当前原子的start_ms调整为前一个原子的end_ms

        保守策略，不修改已有的时间点，只调整当前原子
        """
        new_start_ms = prev_atom.end_ms

        # 检查是否会导致时长为0或负数
        if new_start_ms >= atom.end_ms:
            # 极端情况：前一个原子完全覆盖当前原子
            # 缩短前一个原子，给当前原子留出至少1秒空间
            logger.warning(f"  {atom.atom_id} 被完全覆盖，调整前一个原子边界")

            # 回到前面修改前一个原子
            # 这种情况下，使用比例分割更合适
            return self._proportional_split(prev_atom, atom)[1]

        new_duration_ms = atom.end_ms - new_start_ms

        # 如果调整后时长太短（<500ms），可能需要警告
        if new_duration_ms < 500:
            logger.warning(f"  {atom.atom_id} 调整后时长很短: {new_duration_ms}ms")

        # 创建新的Atom对象（Pydantic模型是不可变的）
        fixed_atom = Atom(
            atom_id=atom.atom_id,
            start_ms=new_start_ms,
            end_ms=atom.end_ms,
            duration_ms=new_duration_ms,
            merged_text=atom.merged_text,
            type=atom.type,
            completeness=atom.completeness,
            source_utterance_ids=atom.source_utterance_ids
        )

        return fixed_atom

    def _proportional_split(self, prev_atom: Atom, atom: Atom) -> tuple[Atom, Atom]:
        """
        策略2: 按比例分割重叠区域
        在重叠区域的中点处分割，同时调整两个原子

        更激进的策略，但可能更公平
        """
        overlap_start = atom.start_ms
        overlap_end = prev_atom.end_ms
        midpoint = (overlap_start + overlap_end) // 2

        # 修复前一个原子（缩短end_ms）
        fixed_prev = Atom(
            atom_id=prev_atom.atom_id,
            start_ms=prev_atom.start_ms,
            end_ms=midpoint,
            duration_ms=midpoint - prev_atom.start_ms,
            merged_text=prev_atom.merged_text,
            type=prev_atom.type,
            completeness=prev_atom.completeness,
            source_utterance_ids=prev_atom.source_utterance_ids
        )

        # 修复当前原子（延后start_ms）
        fixed_atom = Atom(
            atom_id=atom.atom_id,
            start_ms=midpoint,
            end_ms=atom.end_ms,
            duration_ms=atom.end_ms - midpoint,
            merged_text=atom.merged_text,
            type=atom.type,
            completeness=atom.completeness,
            source_utterance_ids=atom.source_utterance_ids
        )

        return fixed_prev, fixed_atom

    def get_overlap_report(self, atoms_before: List[Atom], atoms_after: List[Atom]) -> dict:
        """
        生成修复报告

        Args:
            atoms_before: 修复前的原子
            atoms_after: 修复后的原子

        Returns:
            报告字典
        """
        overlaps_before = self._count_overlaps(atoms_before)
        overlaps_after = self._count_overlaps(atoms_after)

        return {
            'overlaps_before': overlaps_before,
            'overlaps_after': overlaps_after,
            'fixed_count': overlaps_before - overlaps_after,
            'strategy': self.strategy
        }

    def _count_overlaps(self, atoms: List[Atom]) -> int:
        """统计重叠数量"""
        if len(atoms) <= 1:
            return 0

        atoms_sorted = sorted(atoms, key=lambda a: a.start_ms)
        count = 0

        for i in range(len(atoms_sorted) - 1):
            if atoms_sorted[i].end_ms > atoms_sorted[i + 1].start_ms:
                count += 1

        return count
