"""
测试工具函数
"""

import sys
from pathlib import Path
import os

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import ClaudeClient, save_json, load_json, setup_logger
from config import CLAUDE_API_KEY


def test_logger():
    """测试日志"""
    print("\n测试日志系统...")
    logger = setup_logger("test", "logs/test.log")
    logger.info("这是一条INFO日志")
    logger.warning("这是一条WARNING日志")
    logger.error("这是一条ERROR日志")
    print("OK 日志系统正常")


def test_file_utils():
    """测试文件工具"""
    print("\n测试文件工具...")

    # 测试保存和加载
    test_data = {"test": "data", "number": 123}
    save_json(test_data, "data/processed/test.json")
    loaded = load_json("data/processed/test.json")

    assert loaded == test_data
    print("OK 文件工具正常")

    # 清理
    os.remove("data/processed/test.json")


def test_claude_client():
    """测试Claude客户端"""
    if not CLAUDE_API_KEY:
        print("\nWARNING 未配置CLAUDE_API_KEY，跳过API测试")
        print("提示：请在项目根目录创建 .env 文件并添加：")
        print("CLAUDE_API_KEY=your_api_key_here")
        return

    print("\n测试Claude客户端...")
    client = ClaudeClient(CLAUDE_API_KEY)

    # 简单测试
    response = client.call("请回复：测试成功", max_tokens=100)
    print(f"API响应: {response}")

    # 统计
    stats = client.get_stats()
    print(f"统计: {stats}")

    print("OK Claude客户端正常")


if __name__ == "__main__":
    test_logger()
    test_file_utils()
    test_claude_client()
    print("\n" + "="*60)
    print("工具函数测试完成！")
    print("="*60)
