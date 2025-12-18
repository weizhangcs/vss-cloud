from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# 复用 Narration 的原子结构，保持全链路数据一致性
from ai_services.biz_services.narration.schemas import NarrationSnippet


class DubbingServiceParams(BaseModel):
    """
    [Service Params] 配音服务参数
    """
    template_name: str = Field(..., description="TTS 模板名称 (e.g. 'google_en_dialogue', 'aliyun_zh_news')")
    target_lang: str = Field(..., description="目标语言代码 (e.g. 'en', 'zh')")

    # 导演参数 (可选)
    style: str = Field(default="cinematic", description="配音风格 (用于导演提示词)")
    perspective: str = Field(default="objective", description="叙事视角 (用于导演提示词)")

    debug: bool = False


class DubbingTaskPayload(BaseModel):
    """
    [Payload] Dubbing 任务载荷
    """
    # 输入：Narration 的产出物
    absolute_input_narration_path: str = Field(..., description="Narration 结果文件绝对路径")

    # 输入：Dataset 蓝图 (用于获取角色性别、场景氛围等上下文)
    blueprint_path: str = Field(..., description="NarrativeDataset 蓝图文件路径")

    # 输出：Dubbing 结果 JSON 的路径
    absolute_output_path: str = Field(..., description="结果输出绝对路径")

    service_params: DubbingServiceParams


class DubbingSnippetResult(NarrationSnippet):
    """
    [Item Result] 单个片段的配音结果
    继承自 NarrationSnippet，追加音频信息
    """
    audio_file_path: Optional[str] = Field(None, description="生成的音频文件相对路径")
    duration_seconds: float = Field(0.0, description="音频时长")

    # 导演结果 (回填)
    tts_instruct: Optional[str] = None
    narration_for_audio: Optional[str] = None


class DubbingResult(BaseModel):
    """
    [Final Result] Dubbing 最终交付物
    """
    generation_date: Optional[str] = None
    asset_name: Optional[str] = None

    template_name: str
    total_duration: float

    # 包含音频信息的脚本列表
    dubbing_script: List[DubbingSnippetResult]

    ai_total_usage: Dict[str, Any]