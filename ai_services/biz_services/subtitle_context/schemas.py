from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class SubtitleContextPayload(BaseModel):
    """API 输入载荷"""
    subtitle_path: str = Field(..., description="原始字幕文件路径 (.srt)")

    # [核心上下文]
    known_characters: List[str] = Field(
        default_factory=list,
        description="项目已知的角色列表，如 ['Kate', 'Audrey', 'CEO']。用于辅助 AI 锁定角色。"
    )

    # 辅助信息 (可选)
    video_title: Optional[str] = None
    plot_summary: Optional[str] = None  # 如果有剧情大纲，扔进去效果更好

    # 模型配置
    model_name: str = "gemini-2.0-flash-exp"  # 推荐使用 2.0 Flash 或 1.5 Pro，因为需要长窗口
    lang: str = "en"


class OptimizedSubtitleItem(BaseModel):
    """优化后的字幕行"""
    index: int = Field(..., description="原始或新的序号")
    start_time: float
    end_time: float
    content: str = Field(..., description="合并/清洗后的完整对白")
    speaker: str = Field(..., description="推测的角色名")
    reasoning: Optional[str] = Field(None, description="AI 的推测理由 (可选，用于调试)")

class CharacterMetric(BaseModel):
    """单角色分析指标"""
    name: str = Field(..., description="展示用的标准名")
    key: str = Field(..., description="归一化Key")
    weight_score: float = Field(..., description="综合重要度得分")
    weight_percent: str = Field(..., description="相对百分比 (用于UI)")
    stats: Dict[str, Any] = Field(..., description="统计细节: lines, duration_sec")
    variations: List[str] = Field(default_factory=list, description="出现的各种拼写")


class SubtitleContextResult(BaseModel):
    """服务产出 (增强版)"""
    input_file: str

    # AI 优化后的字幕列表
    optimized_subtitles: List[OptimizedSubtitleItem]

    # [新增] 还原的 ASS 文件路径 (相对路径)
    output_ass_path: Optional[str] = None

    # [新增] 角色分析报告
    character_roster: List[CharacterMetric] = Field(default_factory=list)

    stats: Dict[str, Any]
    usage_report: Dict[str, Any]