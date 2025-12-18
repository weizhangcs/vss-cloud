"""
VSS Narrative Dataset Schema (Production Ready).
Location: ai_services/biz_services/narrative_dataset.py

Design Standards:
1. [Strict Validation] All inputs strictly typed. Extra fields are forbidden (typo protection).
2. [Normalized] Decoupled Scene/Chapter relationships.
3. [Logical Integrity] StoryNode includes reference anchors (ref_scene_id).
4. [Pydantic V2] Uses ConfigDict and computed_field for modern serialization.
"""

import uuid
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, model_validator, ConfigDict, computed_field

# ==============================================================================
# 0. 私有工具 (Private Helpers)
# ==============================================================================

def _parse_timestamp(time_str: str) -> float:
    """
    [Internal] 解析 'HH:MM:SS.mmm' -> float seconds.
    Fail-safe: returns 0.0 on error.
    """
    if not time_str:
        return 0.0
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return 0.0
    except Exception:
        return 0.0

# ==============================================================================
# 1. 枚举类型 (Enums)
# ==============================================================================

class CaptionType(str, Enum):
    PERSON = "Person"
    LOCATION = "Location"
    TIME = "Time"
    SCENE_SUMMARY = "Scene_Summary"
    OTHER = "Other"

    @classmethod
    def _missing_(cls, value): return cls.OTHER

class HighlightType(str, Enum):
    ACTION = "Action"
    THRILLER = "Thriller"
    SUSPENSE = "Suspense"
    EMOTIONAL = "Emotional"
    COMEDY = "Comedy"
    OTHER = "Other"

class SceneContentType(str, Enum):
    DIALOGUE_HEAVY = "Dialogue_Heavy"
    ACTION = "Action"
    INTERNAL_MONOLOGUE = "Internal_Monologue"
    MONTAGE = "Montage"
    ESTABLISHING_SHOT = "Establishing_Shot"
    UNKNOWN = "Unknown"

    @classmethod
    def _missing_(cls, value): return cls.UNKNOWN

class NarrativeFunction(str, Enum):
    LINEAR = "LINEAR"
    FLASHBACK = "FLASHBACK"       # Relative to ref_scene_id (Trigger)
    FLASHFORWARD = "FLASHFORWARD" # Relative to ref_scene_id (Foreshadowing)
    INTERCUT = "INTERCUT"         # Relative to ref_scene_id (Parallel)
    DIVERGENCE_POINT = "DIVERGENCE"

# ==============================================================================
# 2. 基础原子对象 (Atoms)
# ==============================================================================

class BaseSchema(BaseModel):
    """
    工程基类：禁止未知字段输入，防止拼写错误被静默忽略。
    """
    model_config = ConfigDict(extra='forbid')

class DialogueItem(BaseSchema):
    content: str = Field(..., description="台词内容")
    speaker: str = Field(..., description="说话人")
    start_time: str = Field(..., description="HH:MM:SS.mmm")
    end_time: str = Field(..., description="HH:MM:SS.mmm")

class CaptionItem(BaseSchema):
    content: str = Field(..., description="文本内容")
    type: CaptionType = Field(..., description="类型")
    start_time: str = Field(...)
    end_time: str = Field(...)

class HighlightItem(BaseSchema):
    description: str = Field(..., description="看点描述")
    type: HighlightType = Field(..., description="类型")
    start_time: str = Field(...)
    end_time: str = Field(...)
    tags: List[str] = Field(..., description="标签(无则空列表)")

# ==============================================================================
# 3. 项目元数据 (Metadata)
# ==============================================================================

class ProjectMetadata(BaseSchema):
    asset_name: str = Field(..., description="媒资名称")
    project_name: str = Field(..., description="标注项目名称")
    version: str = Field(..., description="版本号 (e.g. '1.0')")
    issue_date: str = Field(..., description="发行日期 (ISO8601)")
    annotator: str = Field(..., description="责任人 (无则空串)")
    description: str = Field(..., description="描述 (无则空串)")

# ==============================================================================
# 4. 物理层 (Physical Layer)
# ==============================================================================

class NarrativeScene(BaseSchema):
    """
    [物理场景实体]
    包含所有客观事实。使用 @computed_field 自动暴露秒级时间。
    """
    scene_uuid: uuid.UUID = Field(..., description="场景唯一ID")
    local_id: int = Field(..., alias="id", description="内部数字ID")

    # Time (Source of Truth)
    start_time_str: str = Field(..., alias="start_time")
    end_time_str: str = Field(..., alias="end_time")

    # Content
    scene_content_type: SceneContentType = Field(..., alias="scene_content_type")
    dialogues: List[DialogueItem] = Field(...)
    captions: List[CaptionItem] = Field(...)
    highlights: List[HighlightItem] = Field(...)

    # Description
    inferred_location: str = Field(..., description="地点")
    character_dynamics: str = Field(..., description="动态")
    mood_and_atmosphere: str = Field(..., description="氛围")

    # --- Computed Properties (Pydantic V2 Style) ---
    # 这些字段不会出现在 Input JSON 校验中，但在 dump() 时会自动计算并输出

    @computed_field
    @property
    def start_sec(self) -> float:
        return _parse_timestamp(self.start_time_str)

    @computed_field
    @property
    def end_sec(self) -> float:
        return _parse_timestamp(self.end_time_str)

    @computed_field
    @property
    def duration(self) -> float:
        s = self.start_sec
        e = self.end_sec
        return round(max(0.0, e - s), 3)

class NarrativeChapter(BaseSchema):
    """
    [章节索引]
    Scene -> Chapter 关系的唯一维护者。
    """
    chapter_uuid: uuid.UUID = Field(..., description="章节唯一ID")
    local_id: int = Field(..., description="章节序号")
    name: str = Field(..., description="章节标题")
    scene_ids: List[str] = Field(..., description="包含的场景ID列表")

# ==============================================================================
# 5. 逻辑层 (Logical Layer)
# ==============================================================================

class StoryNode(BaseSchema):
    """
    [叙事节点]
    包含 ref_scene_id 以支持非线性叙事锚点。
    """
    local_id: int = Field(..., description="引用的 Scene ID")
    narrative_index: int = Field(..., description="播放顺序")
    narrative_function: NarrativeFunction = Field(default=NarrativeFunction.LINEAR)

    # [Revised] 锚点引用 (e.g., Flashback relative to WHO?)
    ref_scene_id: Optional[int] = Field(default=None, description="关联/锚点 Scene ID")

    display_label: Optional[str] = Field(default=None)

class StoryBranch(BaseSchema):
    branch_id: str = Field(default="main")
    name: str = Field(default="Main Story")
    nodes: List[StoryNode] = Field(default_factory=list)

    parent_branch_id: Optional[str] = Field(default=None)
    divergence_index: Optional[int] = Field(default=None)

class NarrativeStoryline(BaseSchema):
    root_branch_id: str = Field(default="main")
    branches: Dict[str, StoryBranch] = Field(default_factory=dict)

# ==============================================================================
# 6. 根数据集 (Root Dataset)
# ==============================================================================

class NarrativeDataset(BaseSchema):
    """
    [VSS Data Contract]
    Strict input validation. No auto-guessing.
    """
    asset_uuid: uuid.UUID = Field(...)
    project_uuid: uuid.UUID = Field(...)
    project_metadata: ProjectMetadata = Field(...)

    # 1. Physical Layer (Mandatory)
    scenes: Dict[str, NarrativeScene] = Field(..., description="Flat Scene Map")
    chapters: Dict[str, NarrativeChapter] = Field(..., description="Chapter Index")

    # 2. Logical Layer (Optional/Loose)
    narrative_storyline: NarrativeStoryline = Field(default_factory=NarrativeStoryline)