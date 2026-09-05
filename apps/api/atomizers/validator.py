"""
原子化质量验证器
"""

from typing import List, Dict, Any
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Atom, Utterance
from utils import setup_logger

logger = setup_logger(__name__)


class AtomValidator:
    """原子化质量验证器"""

    def __init__(self):
        self.issues = []
        self.warnings = []

    def validate(
        self,
        atoms: List[Atom],
        original_utterances: List[Utterance]
    ) -> Dict[str, Any]:
        """
        验证原子化质量

        Returns:
            验证报告
        """
        self.issues = []
        self.warnings = []

        logger.info("开始质量验证...")

        # 验证1: 时间完整性
        coverage = self._check_time_coverage(atoms, original_utterances)

        # 验证2: 时间连续性
        gaps = self._check_time_continuity(atoms)

        # 验证3: 原子长度分布
        length_dist = self._check_length_distribution(atoms)

        # 验证4: 类型分布
        type_dist = self._check_type_distribution(atoms)

        # 验证5: ID连续性
        id_check = self._check_id_continuity(atoms)

        # 验证6: 文本完整性
        text_check = self._check_text_completeness(atoms)

        # 生成报告
        report = {
            "total_atoms": len(atoms),
            "coverage_rate": coverage,
            "time_gaps": gaps,
            "length_distribution": length_dist,
            "type_distribution": type_dist,
            "id_continuous": id_check,
            "text_complete": text_check,
            "issues": self.issues,
            "warnings": self.warnings,
            "quality_score": self._calculate_score()
        }

        return report

    def _check_time_coverage(
        self,
        atoms: List[Atom],
        utterances: List[Utterance]
    ) -> float:
        """检查时间覆盖率"""
        if not utterances:
            return 0.0

        original_duration = utterances[-1].end_ms - utterances[0].start_ms
        atoms_duration = sum(a.duration_ms for a in atoms)

        coverage = atoms_duration / original_duration if original_duration > 0 else 0

        if coverage < 0.85:
            self.issues.append(
                f"时间覆盖率过低: {coverage*100:.1f}% (应>85%)"
            )
        elif coverage < 0.95:
            self.warnings.append(
                f"时间覆盖率偏低: {coverage*100:.1f}% (建议>95%)"
            )

        return coverage

    def _check_time_continuity(self, atoms: List[Atom]) -> List[Dict]:
        """检查时间连续性"""
        gaps = []

        for i in range(len(atoms) - 1):
            gap_ms = atoms[i+1].start_ms - atoms[i].end_ms

            if gap_ms > 30000:  # >30秒
                gaps.append({
                    "from_atom": atoms[i].atom_id,
                    "to_atom": atoms[i+1].atom_id,
                    "gap_seconds": gap_ms / 1000,
                    "severity": "high" if gap_ms > 60000 else "medium"
                })

            if gap_ms < -1000:  # 负间隔（重叠）
                self.issues.append(
                    f"时间重叠: {atoms[i].atom_id} 和 {atoms[i+1].atom_id}"
                )

        if len(gaps) > len(atoms) * 0.1:
            self.warnings.append(
                f"时间间隔过多: {len(gaps)}个大间隔 (>10%)"
            )

        return gaps

    def _check_length_distribution(self, atoms: List[Atom]) -> Dict:
        """检查长度分布"""
        lengths = [a.duration_seconds for a in atoms]

        short = sum(1 for l in lengths if l < 30)
        medium = sum(1 for l in lengths if 30 <= l < 300)
        long_seg = sum(1 for l in lengths if l >= 300)

        dist = {
            "short_(<30s)": short,
            "medium_(30s-5min)": medium,
            "long_(>5min)": long_seg,
            "avg_seconds": sum(lengths) / len(lengths) if lengths else 0,
            "max_seconds": max(lengths) if lengths else 0,
            "min_seconds": min(lengths) if lengths else 0
        }

        # 检查异常
        if dist["avg_seconds"] < 10:
            self.warnings.append("平均原子时长过短 (<10秒)")
        if dist["avg_seconds"] > 180:
            self.warnings.append("平均原子时长过长 (>3分钟)")

        return dist

    def _check_type_distribution(self, atoms: List[Atom]) -> Dict:
        """检查类型分布"""
        type_count = {}
        for atom in atoms:
            type_count[atom.type] = type_count.get(atom.type, 0) + 1

        # 检查是否所有原子类型都相同（可能有问题）
        if len(type_count) == 1:
            self.warnings.append(
                f"所有原子类型相同: {list(type_count.keys())[0]}"
            )

        return type_count

    def _check_id_continuity(self, atoms: List[Atom]) -> bool:
        """检查ID连续性"""
        for i, atom in enumerate(atoms):
            expected_id = f"A{i+1:03d}"
            if atom.atom_id != expected_id:
                self.issues.append(
                    f"ID不连续: 第{i+1}个原子ID为{atom.atom_id}, 期望{expected_id}"
                )
                return False
        return True

    def _check_text_completeness(self, atoms: List[Atom]) -> bool:
        """检查文本完整性"""
        for atom in atoms:
            if not atom.merged_text or len(atom.merged_text.strip()) < 5:
                self.issues.append(
                    f"文本过短或为空: {atom.atom_id}"
                )
                return False

            if len(atom.source_utterance_ids) == 0:
                self.issues.append(
                    f"缺少来源字幕ID: {atom.atom_id}"
                )

        return True

    def _calculate_score(self) -> str:
        """计算质量分数"""
        issue_count = len(self.issues)
        warning_count = len(self.warnings)

        if issue_count == 0 and warning_count == 0:
            return "优秀 (A)"
        elif issue_count == 0 and warning_count <= 3:
            return "良好 (B)"
        elif issue_count <= 2:
            return "合格 (C)"
        else:
            return "不合格 (D)"

    def print_report(self, report: Dict):
        """打印验证报告"""
        print("\n" + "="*60)
        print("原子化质量验证报告")
        print("="*60)

        print(f"\n总原子数: {report['total_atoms']}")
        print(f"时间覆盖率: {report['coverage_rate']*100:.1f}%")
        print(f"质量评分: {report['quality_score']}")

        print(f"\n长度分布:")
        for k, v in report['length_distribution'].items():
            if isinstance(v, float):
                print(f"  {k}: {v:.1f}")
            else:
                print(f"  {k}: {v}")

        print(f"\n类型分布:")
        for k, v in report['type_distribution'].items():
            print(f"  {k}: {v}")

        if report['time_gaps']:
            print(f"\n时间间隔 (>30秒): {len(report['time_gaps'])}个")
            for gap in report['time_gaps'][:5]:
                print(f"  {gap['from_atom']} -> {gap['to_atom']}: {gap['gap_seconds']:.1f}秒")

        if report['issues']:
            print(f"\n[ERROR] 严重问题 ({len(report['issues'])}个):")
            for issue in report['issues']:
                print(f"  - {issue}")

        if report['warnings']:
            print(f"\n[WARNING] 警告 ({len(report['warnings'])}个):")
            for warning in report['warnings']:
                print(f"  - {warning}")

        print("\n" + "="*60)
