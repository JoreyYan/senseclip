"""
测试数据模型
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Utterance, Atom


def test_utterance():
    """测试Utterance模型"""
    utt = Utterance(
        id=1,
        start_ms=8933,
        end_ms=10400,
        text="开始了没有啊",
        duration_ms=1467
    )

    assert utt.id == 1
    assert utt.start_time == "00:00:08"
    assert utt.end_time == "00:00:10"
    print("OK Utterance model test passed")


def test_atom():
    """测试Atom模型"""
    atom = Atom(
        atom_id="A001",
        start_ms=500000,
        end_ms=510000,
        duration_ms=10000,
        merged_text="测试文本",
        type="fragment",
        completeness="完整",
        source_utterance_ids=[1, 2, 3]
    )

    assert atom.atom_id == "A001"
    assert atom.start_time == "00:08:20"
    assert atom.duration_seconds == 10.0
    print("OK Atom model test passed")


if __name__ == "__main__":
    test_utterance()
    test_atom()
    print("\nAll model tests passed!")
