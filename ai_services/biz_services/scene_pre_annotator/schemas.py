# ai_services/biz_services/scene_pre_annotator/schemas.py

from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, model_validator


# ==============================================================================
# Enums
# ==============================================================================

class AssetType(str, Enum):
    FEATURE_FILM = "feature_film"       # 电影 (长场景，慢节奏)
    SERIES_EPISODE = "series_episode"   # 剧集 (叙事主导)
    SHORT_CLIP = "short_clip"           # 短片/切片 (快节奏)
    DOCUMENTARY = "documentary"         # 纪录片
    RAW_FOOTAGE = "raw_footage"         # 素材毛片

# [New Enum] 场景类型 (Scene Type) - 导演视角分类
class SceneType(str, Enum):
    DIALOGUE = "dialogue"           # 对话场 (正反打/多人)
    ACTION = "action"               # 动作场 (追逐/打斗/移动)
    MONTAGE = "montage"             # 蒙太奇 (时空压缩)
    ESTABLISHING = "establishing"   # 建立场 (环境展示)
    EMOTIONAL = "emotional"         # 情绪场 (特写/反应)
    UNKNOWN = "unknown"

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
    timestamp: float = 0.0
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

# [Refactored] Payload - 增加元数据
class ScenePreAnnotatorPayload(BaseModel):
    video_title: str

    # [New Metadata]
    asset_type: AssetType = Field(default=AssetType.SHORT_CLIP, description="Type of the media asset.")
    content_genre: str = Field(default="general", description="Content genre (e.g., 'sci-fi', 'romance').")

    # 兼容 V3.8 的输入字段
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

    # 结果容器
    class Config:
        use_enum_values = True

# ==============================================================================
# Stage 2: Segmentation
# ==============================================================================

# [Refactored] 场景定义 - V4.2 核心升级
class SceneDefinition(BaseModel):
    index: int = Field(..., description="Global scene index.")
    start_slice_id: int
    end_slice_id: int

    # [Upgrade 1] 地点与环境
    primary_location: str = Field(..., description="Main location. Use 'Unknown' if ambiguous.")

    # [Upgrade 2] 核心类型与逻辑
    scene_type: SceneType = Field(..., description="The functional type of this scene.")
    camera_logic: str = Field(...,
                              description="Editing/Camera logic summary (e.g., 'Fast cuts', 'Long take', 'Static', 'Handheld').")

    # [Restored] 物理层：核心事件
    narrative_action: str = Field(..., description="Core event or physical action occurring in this scene.")

    # [Restored] 心理层：角色张力 (回归设计)
    character_dynamics: Optional[str] = Field(None,
                                              description="Relationship status or tension between characters (e.g., 'Hostile', 'Flirtatious'). Return null if no characters.")

    # [Upgrade 4] 视觉氛围 (与 Slice 对齐, Stage 2 需归纳出主导 Tags)
    visual_mood_tags: List[str] = Field(default_factory=list,
                                        description="Dominant visual mood tags for the whole scene.")

    def limit_tags(cls, v):
        return v[:5]  # 简单粗暴，但在展示层最有效

    # 溯源理由
    reason: str = Field(..., description="Why were these slices grouped together?")

class SceneSegmentationResponse(BaseModel):
    scenes: List[SceneDefinition]

class ScenePreAnnotatorResult(BaseModel):
    scenes: List[SceneDefinition]
    annotated_slices: Optional[List[AnnotatedSliceResult]] = None
    stats: Dict[str, Any]
    usage_report: Dict[str, Any]