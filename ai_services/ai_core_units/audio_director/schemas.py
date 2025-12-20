# ai_services/ai_core_units/audio_director/schemas.py

from typing import List
from pydantic import BaseModel, Field

class EnrichedSnippet(BaseModel):
    """单条导演指令"""
    index: int = Field(..., description="对应输入的序号")
    tts_instruct: str = Field(..., description="TTS Prompt in English (tone, speed, emotion)")
    narration_for_audio: str = Field(..., description="Text with embedded tags like [laugh], [sigh]")

class AudioDirectorResponse(BaseModel):
    """导演输出整体结构"""
    enriched_script: List[EnrichedSnippet]