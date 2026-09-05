"""
测试原子化器（前10分钟）
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import SRTParser, Cleaner
from atomizers import Atomizer
from utils import save_jsonl, setup_logger
from config import CLAUDE_API_KEY
import json

logger = setup_logger(__name__)


def test_atomizer_small():
    """测试原子化器（小数据集：前10分钟）"""
    print("\n" + "="*60)
    print("测试原子化器 - 前10分钟")
    print("="*60)

    if not CLAUDE_API_KEY:
        print("ERROR 未配置CLAUDE_API_KEY")
        print("请在项目根目录创建 .env 文件并添加：")
        print("CLAUDE_API_KEY=your_api_key_here")
        return

    # Step 1: 解析字幕
    parser = SRTParser()
    utterances = parser.parse("data/raw/test.srt")

    cleaner = Cleaner()
    utterances = cleaner.clean(utterances)

    # 只取前10分钟（假设前600秒）
    utterances_10min = [u for u in utterances if u.start_ms < 600000]
    print(f"前10分钟字幕数: {len(utterances_10min)}")

    # Step 2: 原子化
    atomizer = Atomizer(CLAUDE_API_KEY, batch_size=50)
    atoms = atomizer.atomize(utterances_10min)

    print(f"\nOK 生成原子数: {len(atoms)}")

    # Step 3: 查看结果
    print("\n前5个原子：")
    for i, atom in enumerate(atoms[:5]):
        print(f"\n{i+1}. {atom.atom_id} [{atom.start_time}-{atom.end_time}] ({atom.duration_seconds:.1f}秒)")
        print(f"   类型: {atom.type}")
        print(f"   完整性: {atom.completeness}")
        print(f"   文本: {atom.merged_text[:80]}...")

    # Step 4: 验证质量
    print("\n质量检查：")

    # 检查1：时间连续性
    time_gaps = []
    for i in range(len(atoms) - 1):
        gap = atoms[i+1].start_ms - atoms[i].end_ms
        if gap > 30000:  # >30秒
            time_gaps.append((atoms[i].atom_id, atoms[i+1].atom_id, gap/1000))

    if time_gaps:
        print(f"  WARNING 发现{len(time_gaps)}个大时间间隔：")
        for a1, a2, gap in time_gaps[:3]:
            print(f"     {a1} -> {a2}: {gap:.1f}秒")
    else:
        print(f"  OK 时间连续性良好")

    # 检查2：类型分布
    type_count = {}
    for atom in atoms:
        type_count[atom.type] = type_count.get(atom.type, 0) + 1

    print(f"  类型分布:")
    for t, c in type_count.items():
        print(f"     {t}: {c}个")

    # 检查3：完整片段
    complete_segments = [a for a in atoms if a.type == "complete_segment"]
    print(f"  完整片段: {len(complete_segments)}个")

    # Step 5: 保存
    save_jsonl(atoms, "data/processed/atoms_10min.jsonl")
    print(f"\nOK 已保存到 data/processed/atoms_10min.jsonl")

    # Step 6: API统计
    stats = atomizer.client.get_stats()
    print(f"\nAPI统计:")
    print(f"  调用次数: {stats['total_calls']}")
    print(f"  输入tokens: {stats['total_input_tokens']}")
    print(f"  输出tokens: {stats['total_output_tokens']}")
    print(f"  预估成本: {stats['estimated_cost']}")

    print("\n" + "="*60)
    print("测试完成！请人工检查 data/processed/atoms_10min.jsonl")
    print("="*60)

    return atoms


if __name__ == "__main__":
    atoms = test_atomizer_small()
