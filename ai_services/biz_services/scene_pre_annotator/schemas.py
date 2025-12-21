# ai_services/biz_services/scene_pre_annotator/schemas.py

from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field

# ==============================================================================
# Enums
# ==============================================================================

class ShotType(str, Enum):
    """运镜的类别"""
    EXTREME_CLOSE_UP = "extreme_close_up"
    CLOSE_UP = "close_up"
    MEDIUM_SHOT = "medium_shot"
    LONG_SHOT = "long_shot"
    ESTABLISHING_SHOT = "establishing_shot"

class VisualMood(str, Enum):
    """视觉的情绪氛围"""
    NEUTRAL = "neutral"
    WARM = "warm"
    COLD = "cold"
    DARK_TENSE = "dark_tense"
    BRIGHT_CHEERFUL = "bright_cheerful"

# ==============================================================================
# Stage 1: Batch Visual Inference
# ==============================================================================

class VisualAnalysisOutput(BaseModel):
    """单张/组图片的视觉分析结果"""
    slice_id: int = Field(..., description="The ID of the slice being analyzed.")
    shot_type: ShotType = Field(..., description="The camera shot size.")
    subject: str = Field(..., description="Main subject (Person/Object). Keep brief.")
    action: str = Field(..., description="Physical action occurring. Keep brief.")
    mood: VisualMood = Field(..., description="Lighting and atmospheric mood.")

class BatchVisualOutput(BaseModel):
    """批量推理的返回容器"""
    results: List[VisualAnalysisOutput]

# ==============================================================================
# Service I/O
# ==============================================================================

class FrameRef(BaseModel):
    timestamp: float
    path: str
    position: Literal["start", "mid", "end"]

class SliceInput(BaseModel):
    slice_id: int
    start_time: float
    end_time: float
    # Edge 端无论判定为什么类型，都会传 frames 和 text(如有)
    type: str
    frames: List[FrameRef] = Field(default_factory=list)
    text_content: Optional[str] = None

class AnnotatedSliceResult(BaseModel):
    """中间态：融合了视觉分析与原始文本"""
    slice_id: int
    start_time: float
    end_time: float
    type: str
    text_content: Optional[str] = None # 原始字幕透传
    visual_analysis: Optional[VisualAnalysisOutput] = None # 视觉推理结果

class ScenePreAnnotatorPayload(BaseModel):
    video_title: str
    slices: List[SliceInput]
    injected_annotated_slices: Optional[List[AnnotatedSliceResult]] = None
    visual_model: str = "gemini-2.5-flash"
    text_model: str = "gemini-2.5-flash"
    lang: str = "zh"
    temperature: float = 0.1
    debug: bool = False

# ==============================================================================
# Stage 2: Segmentation
# ==============================================================================

class SceneDefinition(BaseModel):
    index: int
    start_slice_id: int
    end_slice_id: int
    reason: str = Field(..., description="Reason for segmentation (e.g. 'Flashback detected', 'New topic').")
    summary: str = Field(..., description="Plot summary of the scene.")
    visual_style: str = Field(..., description="Summary of visual style.")

class SceneSegmentationResponse(BaseModel):
    scenes: List[SceneDefinition]

class ScenePreAnnotatorResult(BaseModel):
    scenes: List[SceneDefinition]
    annotated_slices: Optional[List[AnnotatedSliceResult]] = None
    stats: Dict[str, Any]
    usage_report: Dict[str, Any]