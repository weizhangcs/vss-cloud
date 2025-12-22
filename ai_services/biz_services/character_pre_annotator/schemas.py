# ai_services/biz_services/character_pre_annotator/schemas.py

from typing import List, Dict, Any, Optional
from pathlib import Path
from pydantic import BaseModel, Field, field_validator


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

    # [新增] 批处理大小控制
    batch_size: int = Field(default=150, ge=10, le=500, description="字幕批处理行数")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)

    @field_validator('subtitle_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        """
        [安全校验]
        1. 允许 gs:// 开头的云端路径。
        2. 允许 相对路径 (e.g. 'tmp/file.srt')。
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
                f"Security Error: Absolute paths are not allowed. Please use a relative path (e.g., 'tmp/file.srt'). Received: {v}")

        # 防止向上的路径遍历 (e.g., ../../etc/passwd)
        if ".." in v:
            raise ValueError("Security Error: Path traversal ('..') is not allowed.")

        return v


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