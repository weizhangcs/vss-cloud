"""
Narration Service Schemas.

本模块定义了 Narration 服务的所有数据契约，包括：
1. ControlParams: 输入控制参数与自定义提示词校验。
2. NarrationServiceConfig: 服务内部流转的完整上下文配置。
3. NarrationResult: 最终输出的标准化结果结构。
"""
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, field_validator, ValidationInfo, ConfigDict

# [核心依赖] 引入公共数据基座
# 确保此时 narrative_dataset.py 已经是那个 Strict Mode 的版本
from ai_services.biz_services.narrative_dataset import NarrativeDataset

# ==============================================================================
# 0. 任务输入载荷定义 (Input Payload Contract)
# ==============================================================================

class NarrationTaskPayload(BaseModel):
    """
    [入口契约] Narration 任务的原始输入载荷。
    Handler 接收并校验这些基本路径是否合法。
    """
    asset_name: str = Field(..., description="资产名称")
    asset_id: str = Field(..., description="资产 ID")

    absolute_output_path: str = Field(..., description="结果输出绝对路径")

    # [Update] 这里的 blueprint 指向的是 NarrativeDataset JSON 文件
    absolute_blueprint_path: str = Field(..., description="NarrativeDataset JSON 文件绝对路径")

    service_params: Dict[str, Any] = Field(default_factory=dict, description="用户覆盖的配置参数")

    @field_validator('absolute_blueprint_path')
    @classmethod
    def blueprint_must_exist(cls, v: str) -> str:
        path = Path(v)
        if not path.is_file():
            raise ValueError(f"Dataset file does not exist at path: {v}")
        return v

# ==============================================================================
# 1. 细粒度控制参数定义 (Control & Customization)
# ==============================================================================

class CustomPrompts(BaseModel):
    """自定义提示词容器"""
    narrative_focus: Optional[str] = None
    style: Optional[str] = None

class ScopeParams(BaseModel):
    """范围控制"""
    type: Literal["full", "episode_range", "scene_selection"] = "full"
    value: Optional[List[int]] = None

class CharacterFocusParams(BaseModel):
    """角色聚焦"""
    mode: Literal["all", "specific"] = "all"
    characters: List[str] = Field(default_factory=list)

class ControlParams(BaseModel):
    """
    [核心控制层]
    """
    narrative_focus: str = "general"
    scope: ScopeParams = Field(default_factory=ScopeParams)
    character_focus: CharacterFocusParams = Field(default_factory=CharacterFocusParams)
    style: str = "objective"
    perspective: Literal["third_person", "first_person"] = "third_person"
    perspective_character: Optional[str] = None
    target_duration_minutes: Optional[int] = None
    custom_prompts: Optional[CustomPrompts] = None

    @field_validator('custom_prompts')
    @classmethod
    def validate_custom_usage(cls, v: Optional[CustomPrompts], info: ValidationInfo) -> Optional[CustomPrompts]:
        if not info.data: return v
        focus = info.data.get('narrative_focus')
        style = info.data.get('style')
        if focus == 'custom' and (not v or not v.narrative_focus):
            raise ValueError("Focus is custom but prompt missing.")
        if style == 'custom' and (not v or not v.style):
            raise ValueError("Style is custom but prompt missing.")
        return v

# ==============================================================================
# 2. 服务配置契约 (Service Configuration)
# ==============================================================================

class NarrationServiceConfig(BaseModel):
    """
    [服务契约] Generator 上下文。
    """
    # 允许额外的字段 (如上游 Handler 塞入的一些临时 metadata)
    model_config = ConfigDict(extra='ignore')

    asset_name: Optional[str] = None
    asset_id: Optional[str] = None

    lang: Literal["zh", "en"] = "zh"
    target_lang: Optional[Literal["zh", "en", "fr"]] = None
    model: str = "gemini-2.5-flash"
    rag_top_k: int = Field(default=50, ge=1, le=200)

    speaking_rate: float = 4.2
    overflow_tolerance: float = Field(default=0.0)

    control_params: ControlParams = Field(default_factory=ControlParams)

    # [核心联动] 强类型 Dataset
    # 当 Handler 将 dict 传给这个字段时，Pydantic 会尝试调用 NarrativeDataset(**dict)
    # 这意味着 Strict Mode 的校验会在这里触发。
    # 如果校验失败，_validate_config 会捕获 ValidationError。
    narrative_dataset: Optional[NarrativeDataset] = Field(
        default=None,
        description="Strictly validated Narrative Dataset"
    )

# ==============================================================================
# 3. 结果交付契约 (Output Definition)
# ==============================================================================

class NarrationSnippet(BaseModel):
    """原子单元"""
    narration: str
    narration_source: Optional[str] = None

    # 对应 NarrativeScene.local_id (int)
    source_scene_ids: List[int]

    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator('narration')
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip(): raise ValueError("Empty narration")
        return v

class NarrationResult(BaseModel):
    """最终交付物"""
    generation_date: str
    asset_name: str
    source_corpus: str
    rag_context_snapshot: Optional[str] = None
    narration_script: List[NarrationSnippet]
    ai_total_usage: Dict[str, Any]

    @field_validator('narration_script')
    @classmethod
    def script_must_not_be_empty(cls, v: List[NarrationSnippet]) -> List[NarrationSnippet]:
        if not v: raise ValueError("Empty script")
        return v