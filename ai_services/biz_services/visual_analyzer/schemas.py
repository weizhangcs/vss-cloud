from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class VisualFrameInput(BaseModel):
    frame_id: str = Field(..., description="Unique identifier for the frame to map results back")
    path: str = Field(..., description="GCS URI or local path to the image")
    digest: Optional[str] = Field(None, description="Optional file digest for validation")

class VisualAnalyzerPayload(BaseModel):
    lang: str = Field("en", description="Language code for prompt and response")
    visual_model: str = Field(..., description="Gemini model name")
    frames: Optional[List[VisualFrameInput]] = Field(None, description="Direct list of frames (Debug/Small Batch)")
    frames_file_path: Optional[str] = Field(None,description="Path to external JSON file containing frames list (Production)")

    @model_validator(mode='after')
    def check_frames_source(self):
        if not self.frames and not self.frames_file_path:
            raise ValueError("Either 'frames' or 'frames_file_path' must be provided.")
        return self

class ShotType(str, Enum):
    EXTREME_CLOSE_UP = "extreme_close_up"
    CLOSE_UP = "close_up"
    MEDIUM_CLOSE_UP = "medium_close_up"
    MEDIUM_SHOT = "medium_shot"
    MEDIUM_LONG_SHOT = "medium_long_shot"
    LONG_SHOT = "long_shot"
    EXTREME_LONG_SHOT = "extreme_long_shot"
    ESTABLISHING_SHOT = "establishing_shot"
    OTHER = "other"

# 官方翻译映射表 (方便下游 UI 展示或 Prompt 辅助)
SHOT_TYPE_LABELS = {
    "zh": {
        ShotType.EXTREME_CLOSE_UP: "大特写",
        ShotType.CLOSE_UP: "特写",
        ShotType.MEDIUM_CLOSE_UP: "近景",
        ShotType.MEDIUM_SHOT: "中景",
        ShotType.MEDIUM_LONG_SHOT: "中远景",
        ShotType.LONG_SHOT: "远景",
        ShotType.EXTREME_LONG_SHOT: "大远景",
        ShotType.ESTABLISHING_SHOT: "建立镜头",
        ShotType.OTHER: "其他"
    },
    "en": {
        ShotType.EXTREME_CLOSE_UP: "Extreme Close Up",
        ShotType.CLOSE_UP: "Close Up",
        ShotType.MEDIUM_CLOSE_UP: "Medium Close Up",
        ShotType.MEDIUM_SHOT: "Medium Shot",
        ShotType.MEDIUM_LONG_SHOT: "Medium Long Shot",
        ShotType.LONG_SHOT: "Long Shot",
        ShotType.EXTREME_LONG_SHOT: "Extreme Long Shot",
        ShotType.ESTABLISHING_SHOT: "Establishing Shot",
        ShotType.OTHER: "Other"
    }
}

class VisualAnalysisData(BaseModel):
    shot_type: Optional[ShotType] = Field(None, description="Main shot size")
    environment: Optional[str] = Field(None, description="Physical environment (e.g., Indoor-Bedroom, Outdoor-Street)")
    subject: Optional[str] = None
    action: Optional[str] = None
    lighting_time: Optional[str] = Field(None, description="Time or lighting characteristics (e.g., Day, Night, Dusk)")
    visual_mood_tags: List[str] = Field(default_factory=list)
    
    class Config:
        extra = "allow"

class FrameAnalysisResult(BaseModel):
    frame_id: str
    visual_analysis: VisualAnalysisData

class BatchVisualOutput(BaseModel):
    results: List[FrameAnalysisResult]