# ai_services/biz_services/scene_pre_annotator/schemas.py

from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, model_validator


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

# ==============================================================================
# Stage 1: Batch Visual Inference
# ==============================================================================

class VisualAnalysisOutput(BaseModel):
    """单张/组图片的视觉分析结果"""
    slice_id: int = Field(..., description="The ID of the slice being analyzed.")
    shot_type: ShotType = Field(..., description="The camera shot size.")
    subject: str = Field(..., description="Main subject (Person/Object). Keep brief.")
    action: str = Field(..., description="Physical action occurring. Keep brief.")

    # [New] 动态标签列表 (V4.1)
    visual_mood_tags: List[str] = Field(
        default_factory=list,
        description="A list of 1-3 keywords describing the lighting and atmosphere (e.g., ['warm', 'tense'])."
    )

    @field_validator('visual_mood_tags', mode='before')
    def pre_clean_tags(cls, v):
        """基础清洗，防止 None 或非 List 类型"""
        if v is None:
            return []
        if isinstance(v, str):
            # 容错：如果 LLM 偶尔返回了逗号分隔字符串
            return [t.strip() for t in v.split(',') if t.strip()]
        return v

class BatchVisualOutput(BaseModel):
    """批量推理的返回容器"""
    results: List[VisualAnalysisOutput]

# ==============================================================================
# Service I/O
# ==============================================================================

class FrameRef(BaseModel):
    timestamp: float
    path: str = Field(..., description="图片路径 (支持 gs:// 云端路径或本地绝对路径)")
    position: Literal["start", "mid", "end"]

    @field_validator('path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        """
        [安全校验]
        1. 允许 gs:// 开头的云端路径。
        2. 允许 相对路径 (e.g. 'tmp/frames/1.jpg')。
        3. 【禁止】 绝对路径 (防止路径遍历攻击和环境泄露)。
        """
        v = v.strip()

        # Case A: GCS Path
        if v.startswith("gs://"):
            return v

        # Case B: Local Path
        path_obj = Path(v)

        # 严禁绝对路径 (Windows 或 Linux 格式)
        if path_obj.is_absolute():
            raise ValueError(
                f"Security Error: Absolute paths are not allowed. Please use a relative path. Received: {v}")

        # 防止向上的路径遍历 (e.g., ../../etc/passwd)
        if ".." in v:
            raise ValueError("Security Error: Path traversal ('..') is not allowed.")

        return v

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
    injected_annotated_slices: Optional[List[AnnotatedSliceResult]] = None
    visual_model: str = "gemini-2.5-flash"
    text_model: str = "gemini-2.5-flash"
    lang: str = "zh"
    temperature: float = 0.1
    debug: bool = False

    # 方案 A: 小数据直接传 (保持兼容性)
    slices: Optional[List[SliceInput]] = None

    # 方案 B: 大数据传文件路径 (推荐)
    slices_file_path: Optional[str] = Field(None, description="Path to raw_slices.json (gs:// or local relative)")

    @model_validator(mode='after')
    def check_slices_source(self):
        if not self.slices and not self.slices_file_path:
            raise ValueError("Must provide either 'slices' (list) or 'slices_file_path'.")
        return self

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