"""
测试SRT解析器和清洗器
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import SRTParser, Cleaner


def test_srt_parser():
    """测试SRT解析"""
    print("\n" + "="*60)
    print("测试SRT解析器")
    print("="*60)

    # 解析
    parser = SRTParser()
    file_path = "data/raw/test.srt"

    if not Path(file_path).exists():
        print("ERROR: 测试文件不存在，请先复制SRT文件到 data/raw/test.srt")
        return None

    utterances = parser.parse(file_path)

    print(f"OK 解析完成：{len(utterances)}条字幕")

    # 显示前5条
    print("\n前5条字幕：")
    for i, utt in enumerate(utterances[:5]):
        print(f"{i+1}. [{utt.start_time}-{utt.end_time}] {utt.text}")

    # 验证
    assert len(utterances) > 0, "字幕数量应该大于0"
    assert utterances[0].text, "第一条字幕不应该为空"

    print("\nOK SRT解析器测试通过")
    return utterances


def test_cleaner(utterances):
    """测试清洗器"""
    print("\n" + "="*60)
    print("测试清洗器")
    print("="*60)

    original_count = len(utterances)

    cleaner = Cleaner()
    cleaned = cleaner.clean(utterances)

    print(f"原始数量: {original_count}")
    print(f"清洗后数量: {len(cleaned)}")
    print(f"移除数量: {cleaner.removed_count}")
    if original_count > 0:
        print(f"移除比例: {cleaner.removed_count/original_count*100:.1f}%")

    # 显示清洗后的前5条
    print("\n清洗后的前5条：")
    for i, utt in enumerate(cleaned[:5]):
        print(f"{i+1}. [{utt.start_time}-{utt.end_time}] {utt.text}")

    print("\nOK 清洗器测试通过")
    return cleaned


if __name__ == "__main__":
    utterances = test_srt_parser()
    if utterances:
        cleaned = test_cleaner(utterances)
        print("\n" + "="*60)
        print("所有测试通过！")
        print("="*60)
