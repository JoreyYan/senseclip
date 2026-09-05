"""
文件操作工具
"""

import json
from pathlib import Path
from typing import Any, List
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Utterance, Atom


def save_json(data: Any, file_path: str):
    """保存JSON文件"""
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(file_path: str) -> Any:
    """加载JSON文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_jsonl(items: List[Any], file_path: str):
    """保存JSONL文件（每行一个JSON对象）"""
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        for item in items:
            if hasattr(item, 'model_dump'):  # Pydantic v2
                json_str = json.dumps(item.model_dump(), ensure_ascii=False)
            elif hasattr(item, 'dict'):  # Pydantic v1
                json_str = json.dumps(item.dict(), ensure_ascii=False)
            else:
                json_str = json.dumps(item, ensure_ascii=False)
            f.write(json_str + '\n')


def load_jsonl(file_path: str, model_class=None) -> List[Any]:
    """加载JSONL文件"""
    items = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            if model_class:
                items.append(model_class(**data))
            else:
                items.append(data)
    return items
