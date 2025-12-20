# ai_services/biz_services/scene_pre_annotator/schemas.py

from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


# ==============================================================================
# 1. LLM 交互契约 (面向 Gemini Structured Output)
# ==============================================================================

class VisualAnalysisOutput(BaseModel):
    """[Gemini Vision] 视觉切片分析结果"""
    shot_type: str = Field(..., description="景别: Close-up, Medium Shot, Wide Shot, Long Shot")
    subject: str = Field(..., description="画面主体: Person(Name/Role), Object, Scenery")
    action: str = Field(..., description="动作描述: What is happening?")
    mood: str = Field(..., description="视觉氛围/光影: Dark, Sunny, Tense, Warm")
    is_new_scene: bool = Field(..., description="视觉上是否看起来像是一个新场景的开始")


class SemanticAnalysisOutput(BaseModel):
    """[Gemini Text] 文本切片分析结果"""
    narrative_function: str = Field(..., description="叙事功能: Dialogue, Monologue, Narration, Sound Effect")
    summary: str = Field(..., description="一句话总结这段文本的情节内容")
    potential_scene_change: bool = Field(..., description="从文本语义判断，这里是否发生了场景切换")


# ==============================================================================
# 2. 服务输入/输出契约 (面向 API/Caller)
# ==============================================================================

class FrameRef(BaseModel):
    """单帧引用"""
    timestamp: float
    path: str = Field(..., description="本地文件绝对路径")
    position: Literal["start", "mid", "end"]


class SliceInput(BaseModel):
    """Edge 端传入的切片信息"""
    slice_id: int
    start_time: float
    end_time: float
    type: Literal["dialogue", "visual_segment"]

    # 视觉切片必填
    frames: List[FrameRef] = Field(default_factory=list)
    # 对话切片必填
    text_content: Optional[str] = None


class ScenePreAnnotatorPayload(BaseModel):
    """Service 输入载荷"""
    video_title: str
    slices: List[SliceInput]

    # 配置
    visual_model: str = "gemini-2.5-flash"
    text_model: str = "gemini-2.5-flash"
    lang: str = "zh"
    temperature: float = 0.1


class AnnotatedSliceResult(BaseModel):
    """Service 输出结果"""
    slice_id: int
    start_time: float
    end_time: float
    type: str

    # 融合结果
    visual_analysis: Optional[VisualAnalysisOutput] = None
    semantic_analysis: Optional[SemanticAnalysisOutput] = None

    reasoning: str = "AI Inferred"


class ScenePreAnnotatorResult(BaseModel):
    """最终交付物"""
    annotated_slices: List[AnnotatedSliceResult]
    stats: Dict[str, Any]
    usage_report: Dict[str, Any]