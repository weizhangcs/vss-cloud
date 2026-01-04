# ai_services/biz_services/character_pre_annotator/schemas.py

from typing import List, Dict, Any, Optional
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, ConfigDict, model_validator


# ==============================================================================
# 1. 输入侧载荷契约 (面向 VSS Edge 上传的 JSON 文件内部结构)
# ==============================================================================

class AudioAnalysis(BaseModel):
    gender: str = Field("Unknown", description="Predicted gender: Male, Female, or Unknown")

    # 其他声学特征暂不用于推理，使用 extra='ignore' 忽略或定义为 Optional
    model_config = ConfigDict(extra='ignore')


class SubtitleInputItem(BaseModel):
    """
    VSS Edge 预处理后发送给 Cloud 的标准原子单元。
    JSON 文件应为 List[SubtitleInputItem] 结构。
    """
    index: int = Field(..., description="行号索引")
    content: str = Field(..., description="对白文本内容")
    start_time: float = Field(..., description="起始秒数")
    end_time: float = Field(..., description="结束秒数")
    speaker: str = Field(default="Unknown", description="角色名")
    reasoning: Optional[str] = Field(default=None, description="AI推理依据/置信度说明")
    audio_analysis: Optional[AudioAnalysis] = Field(default=None, description="声学特征分析")
    voice_mood: Optional[str] = Field(default=None, description="AI推断的语气/情感标签 (配音参考)")
    original_indices: Optional[List[int]] = Field(default=None, description="合并前的原始索引列表")


# ==============================================================================
# 2. 任务输入参数契约 (面向 Task Manager API)
# ==============================================================================

class CharacterPreAnnotatorPayload(BaseModel):
    """
    [Input] 角色预处理任务的完整载荷契约。
    """
    # 核心路径：现在指向由 SubtitleInputItem 组成的 JSON 文件
    subtitle_path: Optional[str] = Field(None, description="上传的 JSON 格式字幕数据文件路径")
    subtitles: Optional[List[SubtitleInputItem]] = Field(None, description="直接传入的字幕列表 (Debug模式)")

    # 业务上下文
    known_characters: List[str] = Field(
        default_factory=list,
        description="项目已知的 VIP 角色列表，用于辅助 AI 锁定角色名"
    )
    video_title: Optional[str] = Field(None, description="视频标题，辅助理解剧集背景")

    # 推理配置
    model_name: str = Field(default="gemini-2.5-flash", description="使用的 LLM 模型")
    lang: str = Field(default="zh", description="处理语言")
    batch_size: int = Field(default=150, ge=10, le=500, description="单次批处理行数")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0, description="生成温度")

    @model_validator(mode='after')
    def check_data_source(self):
        if not self.subtitle_path and not self.subtitles:
            raise ValueError("Either 'subtitle_path' or 'subtitles' must be provided.")
        return self

    @field_validator('subtitle_path')
    @classmethod
    def validate_path(cls, v: Optional[str]) -> Optional[str]:
        """安全校验：禁止绝对路径和路径遍历 """
        if v is None:
            return v
        v = v.strip()
        if v.startswith("gs://"):
            return v
        if ".." in v:
            raise ValueError("Security Error: Path traversal ('..') is not allowed.")
        return v


# ==============================================================================
# 3. 输出侧结果契约 (面向 Task Manager 返回值)
# ==============================================================================

class OptimizedSubtitleItem(BaseModel):
    """
    [Increment Result] 单条字幕的 AI 标注增量结果。
    Edge 端将根据 index 将 speaker 和 reasoning 解析回写本地数据库。
    """
    index: int = Field(..., description="对应输入的序号")
    speaker: str = Field(..., description="推断的说话人角色名")
    reasoning: Optional[str] = Field(None, description="AI 的推断依据（可选）")


class CharacterPreAnnotatorResult(BaseModel):
    """
    [Final Output] 角色预标注任务的最终交付物。
    去除了异构的 ASS 文件生成，改为纯 JSON 链路。
    """
    # 结果文件位置：内部承载 List[OptimizedSubtitleItem]
    output_path: str = Field(..., description="生成的 AI 标注 JSON 结果文件路径")

    # 任务元数据
    stats: Dict[str, Any] = Field(..., description="处理行数、批次等统计")
    usage_report: Dict[str, Any] = Field(..., description="AI Token 消耗及成本报告 ")


# ==============================================================================
# 4. LLM 内部交互专用 Schema
# ==============================================================================

class RoleMapping(BaseModel):
    """单行角色的推断结果"""
    index: int
    speaker: str


class BatchRoleInferenceResponse(BaseModel):
    """[Stage 1] 批量角色推断的响应结构"""
    mappings: List[RoleMapping]


class NormalizationItem(BaseModel):
    """角色名归一化项"""
    original_name: str
    normalized_name: str


class SpeakerNormalizationResponse(BaseModel):
    """[Stage 2] 角色名归一化的响应结构"""
    normalization_items: List[NormalizationItem]