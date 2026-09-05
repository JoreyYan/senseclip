"""
数据迁移工具
Data Migration Utility for Legacy to Multi-Video Format
"""

import json
import sys
from pathlib import Path

# 添加路径以便导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.atom import Atom
from models.multi_video_atom import MultiVideoAtom
from managers.project_manager import MultiVideoProjectManager
from models.project import VideoMeta
from datetime import datetime

class DataMigration:
    """数据迁移工具"""

    @staticmethod
    def migrate_legacy_project(
        legacy_data_path: str,
        project_name: str,
        project_description: str = None
    ) -> str:
        """
        将旧格式数据迁移到新的多视频项目格式

        Args:
            legacy_data_path: 旧数据目录路径（包含atoms_full.jsonl等文件）
            project_name: 新项目名称
            project_description: 项目描述

        Returns:
            str: 新项目ID
        """
        print("🚀 开始数据迁移...")
        print(f"旧数据路径: {legacy_data_path}")

        # 1. 创建新项目
        print("\n步骤1: 创建新项目...")
        manager = MultiVideoProjectManager()
        project = manager.create_project(project_name, project_description)
        print(f"✓ 项目ID: {project.project_id}")

        # 2. 读取旧数据
        legacy_path = Path(legacy_data_path)
        atoms_file = legacy_path / "atoms_full.jsonl"

        if not atoms_file.exists():
            raise FileNotFoundError(f"找不到原子数据文件: {atoms_file}")

        print(f"\n步骤2: 读取旧数据...")
        print(f"原子文件: {atoms_file}")

        legacy_atoms = []
        with open(atoms_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    legacy_atoms.append(json.loads(line))

        print(f"✓ 读取到 {len(legacy_atoms)} 个原子")

        # 3. 生成video_id
        video_id = f"v{int(datetime.now().timestamp())}"
        print(f"\n步骤3: 生成视频ID")
        print(f"✓ 视频ID: {video_id}")

        # 4. 转换数据格式
        print(f"\n步骤4: 转换数据格式...")
        multi_atoms = []
        for atom_data in legacy_atoms:
            # 确保有必需字段
            if 'atom_id' not in atom_data:
                continue

            # 创建旧格式原子
            atom = Atom(**atom_data)

            # 转换为新格式
            multi_atom = MultiVideoAtom.from_legacy_atom(
                atom,
                video_id,
                project.project_id
            )
            multi_atoms.append(multi_atom)

        print(f"✓ 转换完成 {len(multi_atoms)} 个原子")

        # 5. 保存到新项目结构
        print(f"\n步骤5: 保存到新项目...")
        video_dir = manager.project_path / "videos" / video_id
        video_dir.mkdir(parents=True, exist_ok=True)

        atoms_file_new = video_dir / "atoms.jsonl"
        with open(atoms_file_new, 'w', encoding='utf-8') as f:
            for atom in multi_atoms:
                f.write(json.dumps(atom.model_dump(), ensure_ascii=False) + '\n')
        print(f"✓ 原子数据已保存: {atoms_file_new}")

        # 6. 创建视频元数据
        print(f"\n步骤6: 创建视频元数据...")
        video_meta = VideoMeta(
            video_id=video_id,
            project_id=project.project_id,
            name=f"迁移自 {legacy_path.name}",
            filename=f"{legacy_path.name}.legacy",
            atom_count=len(multi_atoms),
            processing_status="completed"
        )

        meta_file = manager.project_path / "videos" / f"{video_id}_meta.json"
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(video_meta.model_dump(), f, ensure_ascii=False, indent=2)
        print(f"✓ 视频元数据已保存: {meta_file}")

        # 7. 更新项目统计
        print(f"\n步骤7: 更新项目统计...")
        manager.project_meta.video_count = 1
        manager.project_meta.total_atoms = len(multi_atoms)
        manager.project_meta.updated_at = datetime.now().isoformat()
        manager._save_project_meta()
        print(f"✓ 项目统计已更新")

        print(f"\n{'='*60}")
        print("✅ 迁移完成!")
        print(f"{'='*60}")
        print(f"新项目ID: {project.project_id}")
        print(f"项目路径: {manager.project_path}")
        print(f"迁移视频ID: {video_id}")
        print(f"原子数量: {len(multi_atoms)}")

        return project.project_id

    @staticmethod
    def verify_migration(project_id: str):
        """验证迁移结果"""
        print(f"\n🔍 验证迁移结果: {project_id}")
        print("="*60)

        manager = MultiVideoProjectManager(project_id)

        # 检查项目元数据
        print(f"\n项目信息:")
        print(f"  项目名称: {manager.project_meta.name}")
        print(f"  视频数量: {manager.project_meta.video_count}")
        print(f"  总原子数: {manager.project_meta.total_atoms}")

        # 检查视频数据
        videos = manager.list_videos()
        print(f"\n视频列表:")
        for video in videos:
            print(f"\n  视频ID: {video.video_id}")
            print(f"    名称: {video.name}")
            print(f"    原子数: {video.atom_count}")
            print(f"    状态: {video.processing_status}")

            # 验证原子数据
            atoms_file = manager.project_path / "videos" / video.video_id / "atoms.jsonl"
            if atoms_file.exists():
                atom_count = sum(1 for _ in open(atoms_file, 'r', encoding='utf-8'))
                print(f"    实际原子文件行数: {atom_count}")

                if atom_count != video.atom_count:
                    print(f"    ⚠️  警告: 原子数量不匹配!")
                else:
                    print(f"    ✓ 原子数量匹配")

                # 读取前3个原子验证格式
                print(f"    前3个原子ID:")
                with open(atoms_file, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f):
                        if i >= 3:
                            break
                        atom = json.loads(line)
                        print(f"      - {atom['atom_id']}")
            else:
                print(f"    ✗ 原子文件不存在")

        print(f"\n{'='*60}")
        print("✅ 验证完成")


# CLI 入口
if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    if len(sys.argv) < 3:
        print("="*60)
        print("数据迁移工具 - 使用方法")
        print("="*60)
        print("\n用法:")
        print("  python data_migration.py <旧数据路径> <项目名称> [项目描述]")
        print("\n示例:")
        print("  python data_migration.py data/output '历史视频项目' '迁移的历史视频'")
        print("\n说明:")
        print("  - 旧数据路径: 包含 atoms_full.jsonl 的目录")
        print("  - 项目名称: 新项目的名称")
        print("  - 项目描述: 可选的项目描述")
        sys.exit(1)

    legacy_path = sys.argv[1]
    project_name = sys.argv[2]
    project_desc = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        # 执行迁移
        project_id = DataMigration.migrate_legacy_project(
            legacy_path,
            project_name,
            project_desc
        )

        # 验证
        DataMigration.verify_migration(project_id)

    except Exception as e:
        print(f"\n❌ 迁移失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
