# ai_services/biz_services/localization/schemas.py

from typing import List, Dict, Any, Optional
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, ConfigDict

# 复用 Narration 的原子结构 (保持数据格式统一)
from ai_services.biz_services.narration.schemas import NarrationSnippet


# ==============================================================================
# 1. 任务输入载荷 (Input Payload)
# ==============================================================================

class LocalizationServiceParams(BaseModel):
    """Localization 服务内部参数"""
    source_lang: str = Field(..., description="源语言代码 (e.g. 'en', 'fr')")
    target_lang: str = Field(..., description="目标语言代码 (e.g. 'en', 'fr')")
    model: str = "gemini-2.5-flash"
    debug: bool = False

    # Pacing Checker 参数
    speaking_rate: Optional[float] = Field(default=None, description="目标语言语速 (Word/sec 或 Char/sec)。不填则使用系统默认值。")
    tolerance_ratio: float = 0.1


class LocalizationTaskPayload(BaseModel):
    """
    [入口契约] Localization 任务 Payload
    """
    # 源文件路径
    master_script_path: str = Field(..., description="源 Narration 结果文件路径 (JSON)")
    blueprint_path: str = Field(..., description="NarrativeDataset 蓝图文件路径 (JSON)")

    # 输出控制 (解决“没有产出物”的问题 - 显式指定路径)
    absolute_output_path: str = Field(..., description="结果输出绝对路径")

    # 服务参数
    service_params: LocalizationServiceParams

    @field_validator('master_script_path', 'blueprint_path')
    @classmethod
    def file_must_exist(cls, v: str) -> str:
        # 注意：这里只是简单的字符串校验，文件存在性校验通常在 Handler 执行时做
        if not v.strip():
            raise ValueError("Path cannot be empty")
        return v


# ==============================================================================
# 2. 结果交付契约 (Output Result)
# ==============================================================================

class LocalizationResult(BaseModel):
    """
    [最终交付物] Localization 结果
    结构与 NarrationResult 类似，但强调 source 与 translation 的对照
    """
    generation_date: Optional[str] = None
    asset_name: Optional[str] = None
    source_corpus: Optional[str] = None

    # 语言信息
    source_lang: str
    target_lang: str

    # 上下文快照 (便于追溯翻译依据)
    rag_context_snapshot: Optional[str] = None

    # 核心脚本 (复用 NarrationSnippet)
    # 约定:
    #   item.narration -> 翻译后的文本
    #   item.narration_source -> 翻译前的原文
    narration_script: List[NarrationSnippet]

    ai_total_usage: Dict[str, Any]