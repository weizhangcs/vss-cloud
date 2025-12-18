# ai_services/biz_services/analysis/character/schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


# --- [新增] 输入侧 Schemas (Input Contracts) ---

class CharacterServiceParams(BaseModel):
    """
    角色分析服务的业务参数控制
    """
    characters_to_analyze: List[str] = Field(
        default_factory=list,
        description="需要分析的角色名称列表"
    )
    lang: str = Field(default="zh", description="分析语言 (zh/en)")
    model: str = Field(default="gemini-2.5-flash", description="使用的 LLM 模型")
    temp: float = Field(default=0.1, description="温度系数")

    # 简单的业务规则校验
    @field_validator('temp')
    @classmethod
    def validate_temp(cls, v):
        if not (0.0 <= v <= 1.0):
            raise ValueError("Temperature must be between 0.0 and 1.0")
        return v


class CharacterTaskPayload(BaseModel):
    """
    角色分析任务的完整 Payload 契约
    对应 task.payload 字段
    """
    # 基础文件路径 (由 task_manager API 自动注入)
    absolute_input_file_path: str = Field(..., description="输入 Dataset 文件的绝对路径")
    absolute_output_path: Optional[str] = Field(default=None, description="输出结果文件的绝对路径")

    # 业务参数
    service_params: CharacterServiceParams = Field(default_factory=CharacterServiceParams)


# --- [原有] 输出侧 Schemas (Output Contracts) ---

class IdentifiedFactItem(BaseModel):
    """单条事实的结构契约"""
    scene_id: int
    attribute: str
    value: str = Field(..., description="事实的具体值，强制转换为字符串")
    source_text: str

    class Config:
        extra = "ignore"

    @field_validator('value', mode='before')
    @classmethod
    def validate_value(cls, v):
        return str(v) if v is not None else ""


class CharacterAnalysisResponse(BaseModel):
    """角色分析服务的整体输出契约"""
    identified_facts: List[IdentifiedFactItem]