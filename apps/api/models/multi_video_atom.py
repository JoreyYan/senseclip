"""
多视频原子数据模型
Extended Atom Model for Multi-Video Support
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from .atom import Atom

class MultiVideoAtom(Atom):
    """扩展的原子模型，支持多视频项目"""

    # 新增字段
    video_id: str = Field(..., description="所属视频ID，格式: v{timestamp}")
    project_id: str = Field(..., description="所属项目ID")

    # 重写atom_id以支持新格式
    atom_id: str = Field(..., description="原子ID，格式: {video_id}_A{number}")

    # 跨视频引用
    global_entity_refs: List[str] = Field(default_factory=list, description="引用的全局实体ID列表")
    cross_video_references: List[str] = Field(default_factory=list, description="跨视频引用的其他原子ID")

    @classmethod
    def from_legacy_atom(cls, atom: Atom, video_id: str, project_id: str):
        """从旧格式原子转换为新格式"""
        data = atom.model_dump()

        # 更新atom_id格式
        old_atom_id = data['atom_id']
        new_atom_id = f"{video_id}_{old_atom_id}"

        data.update({
            'atom_id': new_atom_id,
            'video_id': video_id,
            'project_id': project_id,
            'global_entity_refs': [],
            'cross_video_references': []
        })

        return cls(**data)

    def get_video_id(self) -> str:
        """从atom_id中提取video_id"""
        return self.atom_id.split('_A')[0]

    def get_local_atom_number(self) -> int:
        """获取原子在视频内的序号"""
        return int(self.atom_id.split('_A')[1])
