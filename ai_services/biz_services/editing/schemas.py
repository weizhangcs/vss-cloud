from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# [Reference] 引用枚举或其他基础类型 (如果需要)
# from ai_services.biz_services.narrative_dataset import NarrativeDataset

class EditingServiceParams(BaseModel):
    """
    [Service Params] 剪辑服务参数
    """
    default_lang: str = Field("en", description="默认语言 (用于加载 UI 标签)")
    default_model: str = Field("gemini-2.5-flash", description="推理使用的 LLM 模型")
    debug: bool = False

    # 业务参数
    gap_threshold: float = Field(1.0, description="对话连贯性阈值 (秒)")


class EditingTaskPayload(BaseModel):
    """
    [Payload] Editing 任务载荷
    """
    # 输入 1: 配音结果 (提供时序和音频路径)
    absolute_input_dubbing_path: str = Field(..., description="Dubbing 结果文件绝对路径")

    # 输入 2: 剧本蓝图 (提供 B-Roll 素材源信息)
    blueprint_path: str = Field(..., description="NarrativeDataset 蓝图文件路径")

    # 输出
    absolute_output_path: str = Field(..., description="结果输出绝对路径")

    service_params: EditingServiceParams


# --- 内部逻辑使用的 Schema (LLM 响应) ---
class BrollSelectionLLMResponse(BaseModel):
    """B-Roll 选择服务 LLM 的输出契约"""
    selected_ids: List[str]


# --- 最终产出结果 Schema ---

class BrollClip(BaseModel):
    """单个 B-Roll 素材片段"""
    type: str = Field(..., description="类型: dialogue_group / dialogue_single")
    is_group: bool
    scene_id: int
    chapter_id: Optional[str] = Field(None, description="关联的 Chapter UUID (用于 Edge 端剪辑)")
    content: str
    start_time: str
    end_time: str
    duration: float
    original_duration: Optional[float] = None


class EditingSequence(BaseModel):
    """
    一个完整的剪辑序列 (对应一段解说词)
    """
    narration: str
    narration_duration: float
    narration_audio_path: Optional[str] = None
    b_roll_clips: List[BrollClip] = Field(default_factory=list)


class EditingResult(BaseModel):
    """
    [Final Result] Editing 任务最终交付物
    """
    generation_date: Optional[str] = None
    asset_name: Optional[str] = None

    # 核心脚本
    editing_script: List[EditingSequence]

    # 统计信息
    total_sequences: int
    ai_total_usage: Dict[str, Any] = Field(default_factory=dict)