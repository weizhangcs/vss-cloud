from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class SubtitleItem(BaseModel):
    index: int = Field(..., description="Original subtitle index")
    start_time: float = Field(..., description="Start time in seconds")
    end_time: float = Field(..., description="End time in seconds")
    content: str = Field(..., description="Subtitle text content")

class SubtitleMergerPayload(BaseModel):
    lang: str = Field("zh", description="Language code (zh, en)")
    model: str = Field(..., description="LLM model name")
    subtitles: Optional[List[SubtitleItem]] = Field(None, description="List of subtitles to process (Debug)")
    subtitle_file_path: Optional[str] = Field(None,description="Path to external JSON file containing subtitles (Production)")

    @model_validator(mode='after')
    def check_data_source(self):
        if not self.subtitles and not self.subtitle_file_path:
            raise ValueError("Either 'subtitles' or 'subtitle_file_path' must be provided.")
        return self

class MergedSubtitleItem(BaseModel):
    index: int
    start_time: float
    end_time: float
    content: str
    original_indices: List[int] = Field(default_factory=list, description="List of original indices merged into this item")

class SubtitleMergerResponse(BaseModel):
    merged_subtitles: List[MergedSubtitleItem]
    stats: dict
    usage_report: dict


# --- LLM Interaction Schemas (Instruction-Based) ---

class MergeInstruction(BaseModel):
    """A single instruction from the LLM on how to merge a group of subtitles."""
    original_indices: List[int] = Field(..., description="A list of original subtitle indices that should be merged into one.")
    new_content: str = Field(..., description="The new, merged, and punctuated content for this group.")

class MergePlanResponse(BaseModel):
    """The complete merge plan for a batch, as returned by the LLM."""
    merge_plan: List[MergeInstruction]