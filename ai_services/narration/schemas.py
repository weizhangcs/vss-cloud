# ai_services/narration/schemas.py
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, validator


# --- 新增：自定义提示词容器 ---
class CustomPrompts(BaseModel):
    """
    当 standard key 无法满足需求时，通过此对象传入自定义 Prompt 片段。
    支持 {asset_name} 等标准占位符。
    """
    narrative_focus: Optional[str] = Field(
        None,
        description="自定义 RAG 检索意图。例如: '寻找{asset_name}中所有关于美食的特写镜头。'"
    )
    style: Optional[str] = Field(
        None,
        description="自定义 LLM 生成风格。例如: '你是一个说话带口音的老北京，用儿化音解说。'"
    )


class ScopeParams(BaseModel):
    type: Literal["full", "episode_range", "scene_selection"] = "full"
    value: Optional[List[int]] = None


class CharacterFocusParams(BaseModel):
    mode: Literal["all", "specific"] = "all"
    characters: List[str] = Field(default_factory=list)


class ControlParams(BaseModel):
    """控制生成风格和内容的参数"""
    # [修改] 允许 "custom" 或其他字符串，不再强校验枚举，增强灵活性
    narrative_focus: str = "general"

    scope: ScopeParams = Field(default_factory=ScopeParams)
    character_focus: CharacterFocusParams = Field(default_factory=CharacterFocusParams)

    # [修改] 允许 "custom"
    style: str = "objective"

    perspective: Literal["third_person", "first_person"] = "third_person"
    perspective_character: Optional[str] = None
    target_duration_minutes: Optional[int] = None

    # [新增] 自定义提示词字段
    custom_prompts: Optional[CustomPrompts] = None

    # [新增] 校验逻辑：如果选了 custom，必须传对应的文本
    @validator('custom_prompts')
    def validate_custom_usage(cls, v, values):
        focus = values.get('narrative_focus')
        style = values.get('style')

        if focus == 'custom' and (not v or not v.narrative_focus):
            raise ValueError("narrative_focus is 'custom', but custom_prompts.narrative_focus is missing.")

        if style == 'custom' and (not v or not v.style):
            raise ValueError("style is 'custom', but custom_prompts.style is missing.")
        return v


class NarrationServiceConfig(BaseModel):
    """完整的服务配置契约"""
    asset_name: Optional[str] = None
    lang: Literal["zh", "en"] = "zh"
    target_lang: Optional[Literal["zh", "en","fr"]] = None
    model: str = "gemini-2.5-flash"
    rag_top_k: int = Field(default=50, ge=1, le=200)
    speaking_rate: float = 4.2

    # [核心修改] 语义升级：从绝对秒数 -> 相对比例
    # default=0.0 表示严格对齐 (Audio <= Visual)
    # 负数 (e.g., -0.15) 表示预留空间 (Audio <= Visual * 0.85)
    # 正数 (e.g., 0.30) 表示允许溢出 (Audio <= Visual * 1.30)
    overflow_tolerance: float = Field(default=0.0, description="时长容忍度比例。正数允许溢出，负数强制留白。")

    control_params: ControlParams = Field(default_factory=ControlParams)


# ... (NarrationSnippet 和 NarrationResult 保持不变)
class NarrationSnippet(BaseModel):
    """单段解说词结构"""
    # 1. 最终交付文本 (如果是翻译模式，这里存译文；否则存原文)
    narration: str

    # 2. [新增] 源语言文本 (用于对照/字幕)
    # 在翻译模式下，这里存中文原文；非翻译模式下，这里可能为空或与 narration 相同
    narration_source: Optional[str] = None

    # 3. [新增] 配音专用文本
    # 包含 [sigh], [pause] 等标记，专门喂给 TTS
    narration_for_audio: Optional[str] = None

    # 4. [新增] TTS 导演指令
    # 例如 "Speak in a sarcastic tone"
    tts_instruct: Optional[str] = None

    source_scene_ids: List[int]
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator('narration')
    def text_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Narration text cannot be empty")
        return v


class NarrationResult(BaseModel):
    """最终向外交付的完整结果"""
    generation_date: str
    asset_name: str
    source_corpus: str

    # [新增] 持久化 RAG 上下文
    # 未来做“纯翻译任务”时，直接读取这个字段，无需再次检索 RAG
    rag_context_snapshot: Optional[str] = None

    narration_script: List[NarrationSnippet]
    ai_total_usage: Dict[str, Any]

    @validator('narration_script')
    def script_must_not_be_empty(cls, v):
        if not v:
            raise ValueError("Generated narration script is empty!")
        return v

    @validator('narration_script')
    def script_must_not_be_empty(cls, v):
        if not v:
            raise ValueError("Generated narration script is empty!")
        return v