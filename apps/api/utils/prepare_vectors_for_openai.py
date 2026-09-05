"""
为OpenAI准备向量化数据
Prepare Vector Data for OpenAI Embeddings

策略：将原子组合成chunks（5-10个原子/chunk）
- 更好的上下文
- 更低的成本
- 保留原子级别的精确定位信息
"""

import json
import sys
from pathlib import Path
from typing import List, Dict
from datetime import datetime

def ms_to_time(ms: int) -> str:
    """毫秒转时间字符串"""
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis//100}"

def create_chunks(atoms: List[Dict], chunk_size: int = 7) -> List[Dict]:
    """
    将原子组合成chunks

    Args:
        atoms: 原子列表
        chunk_size: 每个chunk包含的原子数（默认7个，约2分钟）

    Returns:
        chunks列表
    """
    chunks = []

    for i in range(0, len(atoms), chunk_size):
        chunk_atoms = atoms[i:i+chunk_size]

        # 构建chunk文本（保留原子ID以便定位）
        chunk_text = ""
        for atom in chunk_atoms:
            chunk_text += f"[{atom['atom_id']}] {atom['merged_text']}\n\n"

        # 计算chunk的时间范围
        start_ms = chunk_atoms[0]['start_ms']
        end_ms = chunk_atoms[-1]['end_ms']
        duration_ms = end_ms - start_ms

        # 构建chunk对象
        chunk = {
            "id": f"CHUNK_{i//chunk_size + 1:03d}",
            "text": chunk_text.strip(),
            "metadata": {
                "chunk_id": f"CHUNK_{i//chunk_size + 1:03d}",
                "atom_ids": [a['atom_id'] for a in chunk_atoms],
                "atom_count": len(chunk_atoms),
                "start_time": ms_to_time(start_ms),
                "end_time": ms_to_time(end_ms),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_seconds": duration_ms / 1000,
                "types": list(set(a['type'] for a in chunk_atoms)),
                "video_id": chunk_atoms[0].get('video_id', 'unknown'),
                # ✅ 优化：只存时间戳和类型，不存文本（避免重复）
                "atoms": [
                    {
                        "atom_id": a['atom_id'],
                        "start_ms": a['start_ms'],
                        "end_ms": a['end_ms'],
                        "duration_seconds": a['duration_ms'] / 1000,
                        "type": a['type']
                        # ❌ 不存text，避免重复！text已经在上面的chunk.text中了
                    }
                    for a in chunk_atoms
                ]
            }
        }

        chunks.append(chunk)

    return chunks

def prepare_for_openai(project_id: str, chunk_size: int = 7):
    """
    为OpenAI准备向量化数据

    Args:
        project_id: 项目ID
        chunk_size: 每个chunk的原子数
    """
    print("="*70)
    print("为OpenAI准备向量化数据")
    print("="*70)

    project_path = Path(f"projects/{project_id}")

    # 收集所有原子
    print("\n步骤1: 收集原子数据...")
    all_atoms = []
    videos_path = project_path / "videos"

    for video_dir in videos_path.iterdir():
        if not video_dir.is_dir():
            continue

        atoms_file = video_dir / "atoms.jsonl"
        if atoms_file.exists():
            video_id = video_dir.name
            with open(atoms_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        atom = json.loads(line)
                        # 添加video_id到metadata
                        atom['video_id'] = video_id
                        all_atoms.append(atom)

    print(f"  ✓ 找到 {len(all_atoms)} 个原子")

    # 创建chunks
    print(f"\n步骤2: 创建chunks（{chunk_size}个原子/chunk）...")
    chunks = create_chunks(all_atoms, chunk_size=chunk_size)
    print(f"  ✓ 创建了 {len(chunks)} 个chunks")

    # 统计信息
    total_duration = sum(c['metadata']['duration_seconds'] for c in chunks)
    avg_duration = total_duration / len(chunks)
    avg_atoms = sum(c['metadata']['atom_count'] for c in chunks) / len(chunks)

    print(f"\n统计信息:")
    print(f"  原子总数: {len(all_atoms)}")
    print(f"  Chunk总数: {len(chunks)}")
    print(f"  总时长: {total_duration/60:.1f}分钟")
    print(f"  平均Chunk时长: {avg_duration:.1f}秒")
    print(f"  平均原子数/Chunk: {avg_atoms:.1f}个")

    # 保存为JSONL格式（OpenAI推荐）
    print(f"\n步骤3: 保存数据...")

    # 保存chunks（用于向量化）
    chunks_file = project_path / "openai_chunks.jsonl"
    with open(chunks_file, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + '\n')
    print(f"  ✓ Chunks已保存: {chunks_file}")
    print(f"    文件大小: {chunks_file.stat().st_size / 1024:.1f} KB")

    # 保存人类可读版本（用于检查）
    readable_file = project_path / "openai_chunks_readable.json"
    with open(readable_file, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 可读版本已保存: {readable_file}")

    # 保存配置
    config = {
        "project_id": project_id,
        "created_at": datetime.now().isoformat(),
        "chunk_size": chunk_size,
        "total_atoms": len(all_atoms),
        "total_chunks": len(chunks),
        "total_duration_minutes": total_duration / 60,
        "avg_chunk_duration_seconds": avg_duration,
        "chunks_file": str(chunks_file),
        "format": "jsonl",
        "encoding": "utf-8"
    }

    config_file = project_path / "openai_chunks_config.json"
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 配置已保存: {config_file}")

    # 展示样例
    print(f"\n样例Chunk:")
    print("-"*70)
    sample = chunks[0]
    print(f"ID: {sample['id']}")
    print(f"原子数: {sample['metadata']['atom_count']}")
    print(f"时间: {sample['metadata']['start_time']} - {sample['metadata']['end_time']}")
    print(f"时长: {sample['metadata']['duration_seconds']:.1f}秒")
    print(f"文本预览: {sample['text'][:200]}...")
    print("-"*70)

    print("\n" + "="*70)
    print("✅ 数据准备完成！")
    print("="*70)
    print(f"\n下一步:")
    print(f"  1. 使用 {chunks_file} 上传到OpenAI")
    print(f"  2. 或者本地生成向量（使用sentence-transformers）")

    return chunks_file, config

# 直接处理现有数据（非项目格式）
def prepare_from_legacy_atoms(atoms_file: str, output_dir: str = ".", chunk_size: int = 7):
    """
    从旧格式的atoms.jsonl直接准备数据

    Args:
        atoms_file: 原子文件路径（如 atoms_full.jsonl）
        output_dir: 输出目录
        chunk_size: 每个chunk的原子数
    """
    print("="*70)
    print("从旧格式原子数据准备向量化数据")
    print("="*70)

    # 读取原子
    print(f"\n加载原子数据: {atoms_file}...")
    atoms = []
    with open(atoms_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                atoms.append(json.loads(line))
    print(f"  ✓ 加载了 {len(atoms)} 个原子")

    # 创建chunks
    print(f"\n创建chunks（{chunk_size}个原子/chunk）...")
    chunks = create_chunks(atoms, chunk_size=chunk_size)
    print(f"  ✓ 创建了 {len(chunks)} 个chunks")

    # 保存
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    chunks_file = output_path / "openai_chunks.jsonl"
    with open(chunks_file, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + '\n')

    print(f"\n✓ Chunks已保存: {chunks_file}")
    print(f"  文件大小: {chunks_file.stat().st_size / 1024:.1f} KB")
    print(f"  Chunk数量: {len(chunks)}")

    # 展示样例
    print(f"\n样例Chunk:")
    print("-"*70)
    sample = chunks[0]
    print(json.dumps(sample, ensure_ascii=False, indent=2)[:500] + "...")
    print("-"*70)

    return chunks_file

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 测试：从现有的atoms_full.jsonl准备数据
    print("测试：准备现有数据...")

    chunks_file = prepare_from_legacy_atoms(
        atoms_file="video_understanding_engine/data/output/atoms_full.jsonl",
        output_dir="video_understanding_engine/data/output",
        chunk_size=7  # 7个原子/chunk，约2分钟
    )

    print(f"\n✅ 完成！")
    print(f"生成的文件: {chunks_file}")
