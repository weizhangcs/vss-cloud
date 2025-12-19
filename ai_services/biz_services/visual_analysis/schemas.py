from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# --- Input / Intermediate Schemas ---

class VisualTag(BaseModel):
    """单帧视觉分析结果"""
    shot_type: str = Field(..., description="Shot scale: Close-up, Wide, etc.")
    subject: str = Field(..., description="Main subject")
    action: str = Field(..., description="Action description")
    mood: str = Field(..., description="Visual atmosphere")
    usability: bool = Field(..., description="Is usable B-roll")


class RawSlice(BaseModel):
    """来自 Edge 端 vss_raw_slices.json 的原始切片"""
    start_time: float
    end_time: float
    type: str = Field(..., description="'dialogue' or 'visual_segment'")
    processing_strategy: str
    text_content: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Cloud 端处理后注入的字段
    visual_analysis: Optional[VisualTag] = None
    thumbnail_path: Optional[str] = None


class VisualAnalysisPayload(BaseModel):
    """API 输入载荷"""
    video_path: str = Field(..., description="视频文件相对路径")
    subtitle_path: Optional[str] = Field(None, description="字幕文件相对路径")
    raw_slices_path: str = Field(..., description="core_slicer 生成的 json 路径")

    # 配置
    visual_model: str = "gemini-2.5-flash"
    semantic_model: str = "gemini-2.5-flash"
    lang: str = "en"


# --- Output Schemas ---

class RefinedSlice(BaseModel):
    """最终聚合后的切片 (VSS-Workbench 消费格式)"""
    start_time: float
    end_time: float
    type: str
    topic: str = Field(..., description="Semantic topic e.g. 'News', 'Transition'")
    content: str = Field(..., description="Merged text or Visual desc")

    # 溯源信息
    source_slice_ids: List[int] = Field(default_factory=list)
    refinement_note: Optional[str] = None

    # 视觉信息 (如果是 Visual Segment)
    visual_tags: Optional[VisualTag] = None
    thumbnail_path: Optional[str] = None


class SubtitleItem(BaseModel):
    """[新增] 字幕轨道单项"""
    start_time: float
    end_time: float
    content: str = Field(..., description="Cleaned and merged subtitle text")
    speaker: str = Field(default="Unknown", description="Inferred speaker name")
    status: str = Field(default="AI_Generated", description="Review status")


class VisualAnalysisResult(BaseModel):
    """服务最终产出"""
    video_path: str
    total_duration: float

    # 轨道 1: 场景/语义时间轴
    timeline: List[RefinedSlice]

    # [新增] 轨道 3: 智能字幕轨道
    subtitle_track: List[SubtitleItem] = Field(default_factory=list)

    stats: Dict[str, Any]
    usage_report: Dict[str, Any]