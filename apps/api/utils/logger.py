"""
日志工具
"""

import logging
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    设置日志器

    Args:
        name: 日志器名称
        log_file: 日志文件路径（可选）

    Returns:
        配置好的Logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 清除已有的handler
    logger.handlers.clear()

    # Rich console handler（彩色输出）
    console_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False
    )
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # 文件handler（可选）
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
