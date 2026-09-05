"""
项目数据模型
Multi-Video Project Data Models
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime
import uuid

class ProjectMeta(BaseModel):
    """项目元数据"""
    project_id: str = Field(default_factory=lambda: f"project_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}")
    name: str = Field(..., description="项目名称")
    description: Optional[str] = Field(None, description="项目描述")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    video_count: int = Field(default=0, description="视频数量")
    total_atoms: int = Field(default=0, description="总原子数")

    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "project_20241201_a1b2c3d4",
                "name": "历史纪录片分析",
                "description": "分析一系列历史主题视频",
                "created_at": "2024-12-01T10:00:00",
                "video_count": 3,
                "total_atoms": 450
            }
        }

class VideoMeta(BaseModel):
    """视频元数据"""
    video_id: str = Field(default_factory=lambda: f"v{int(datetime.now().timestamp())}")
    project_id: str = Field(..., description="所属项目ID")
    name: str = Field(..., description="视频名称")
    filename: str = Field(..., description="原始文件名")
    duration_ms: Optional[int] = Field(None, description="视频时长(毫秒)")
    atom_count: int = Field(default=0, description="原子数量")
    entity_count: int = Field(default=0, description="实体数量")
    processing_status: str = Field(default="pending", description="处理状态: pending|processing|completed|failed")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    srt_path: Optional[str] = Field(None, description="字幕文件路径")

    class Config:
        json_schema_extra = {
            "example": {
                "video_id": "v1733019600",
                "project_id": "project_20241201_a1b2c3d4",
                "name": "历史视频第1集",
                "filename": "history_ep01.mp4",
                "atom_count": 150,
                "processing_status": "completed"
            }
        }

class ProjectStats(BaseModel):
    """项目统计信息"""
    total_videos: int = 0
    total_atoms: int = 0
    unique_entities: int = 0
    cross_video_relationships: int = 0
    total_processing_time_sec: float = 0.0
    storage_size_mb: float = 0.0
