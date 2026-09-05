"""
测试原子化质量验证器
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import SRTParser, Cleaner
from atomizers import AtomValidator
from utils import load_jsonl, save_json, setup_logger
from models import Atom

logger = setup_logger(__name__)


def test_validator():
    """测试验证器"""
    print("\n" + "="*60)
    print("测试原子化质量验证器")
    print("="*60)

    # 加载已生成的原子
    try:
        atoms = load_jsonl("data/processed/atoms_10min.jsonl", Atom)
    except FileNotFoundError:
        print("❌ 请先运行 test_atomizer.py 生成原子数据")
        return

    print(f"加载了 {len(atoms)} 个原子")

    # 加载原始字幕
    parser = SRTParser()
    utterances = parser.parse("data/raw/test.srt")
    cleaner = Cleaner()
    utterances = cleaner.clean(utterances)
    utterances_10min = [u for u in utterances if u.start_ms < 600000]

    print(f"加载了 {len(utterances_10min)} 条原始字幕")

    # 验证
    validator = AtomValidator()
    report = validator.validate(atoms, utterances_10min)

    # 打印报告
    validator.print_report(report)

    # 保存报告
    save_json(report, "data/processed/validation_report.json")
    print("\n[OK] 报告已保存到 data/processed/validation_report.json")

    # 返回判断
    print("\n" + "="*60)
    if report['quality_score'] in ['优秀 (A)', '良好 (B)']:
        print("[PASS] 质量达标，可以进入下一阶段！")
    elif report['quality_score'] == '合格 (C)':
        print("[WARNING] 质量一般，建议优化提示词后重试")
    else:
        print("[FAIL] 质量不合格，必须修复问题后重试")
    print("="*60)

    return report


if __name__ == "__main__":
    report = test_validator()
