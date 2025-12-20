# ai_services/biz_services/character_pre_annotator/schemas.py

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# ==============================================================================
# 1. 任务输入/输出契约 (面向 Task Manager)
# ==============================================================================

class CharacterPreAnnotatorPayload(BaseModel):
    """
    [Input] 任务输入载荷
    """
    subtitle_path: str = Field(..., description="原始字幕文件路径 (.srt)")

    # 核心上下文
    known_characters: List[str] = Field(
        default_factory=list,
        description="项目已知的角色列表 (VIP List)，用于辅助 AI 锁定角色。"
    )

    # 辅助信息
    video_title: Optional[str] = None

    # 模型配置
    model_name: str = "gemini-2.5-flash"  # 适合大批量处理
    lang: str = "zh"


class CharacterMetric(BaseModel):
    """角色统计指标"""
    name: str
    key: str
    weight_score: float
    weight_percent: str
    stats: Dict[str, Any]
    variations: List[str]


class OptimizedSubtitleItem(BaseModel):
    """单行处理结果"""
    index: int
    start_time: float
    end_time: float
    content: str
    speaker: str
    reasoning: Optional[str] = None


class CharacterPreAnnotatorResult(BaseModel):
    """
    [Output] 最终交付物
    """
    input_file: str
    optimized_subtitles: List[OptimizedSubtitleItem]
    output_ass_path: Optional[str] = None
    character_roster: List[CharacterMetric]
    stats: Dict[str, Any]
    usage_report: Dict[str, Any]


# ==============================================================================
# 2. LLM 交互契约 (面向 Gemini)
# ==============================================================================

class RoleMapping(BaseModel):
    """单行角色的推断结果"""
    index: int = Field(..., description="字幕行号")
    speaker: str = Field(..., description="推断的角色名")


class BatchRoleInferenceResponse(BaseModel):
    """[Stage 1] 批量角色推断的响应结构"""
    mappings: List[RoleMapping]


class NormalizationItem(BaseModel):
    original_name: str = Field(..., description="原始出现的角色名")
    normalized_name: str = Field(..., description="标准化后的角色名")

class SpeakerNormalizationResponse(BaseModel):
    """[Stage 2] 角色名归一化的响应结构"""
    normalization_items: List[NormalizationItem]