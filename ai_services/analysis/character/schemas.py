from pydantic import BaseModel, Field
from typing import List

class IdentifiedFactItem(BaseModel):
    """单条事实的结构契约"""
    scene_id: int
    attribute: str
    value: str = Field(..., description="事实的具体值，强制转换为字符串")
    source_text: str

    class Config:
        # 允许 LLM 返回额外的无用字段（如 reason），增强兼容性
        extra = "ignore"

    # [防御] 强制将 value 转为 string，防止 LLM 返回数字导致下游 split() 等方法报错
    @classmethod
    def validate_value(cls, v):
        return str(v)

class CharacterAnalysisResponse(BaseModel):
    """角色分析服务的整体输出契约"""
    identified_facts: List[IdentifiedFactItem]