# 文件名: schemas.py
# 描述: V5.2架构的核心。修正了RAG B生成逻辑中的事实归属BUG。
# 版本: 1.1

import json
from collections import defaultdict

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from django.conf import settings # <-- 导入Django settings
import os

try:
    # --- 修改开始 ---
    # 旧的硬编码路径:
    # with open(r"D:\\DevProjects\\PyCharmProjects\\visify-ae\\resource\\localization\\schemas.json", "r", ...) as f:

    # 新的、相对于Django项目根目录的路径:
    # 我们假设您会把 resource 文件夹放在项目根目录
    i18n_path = os.path.join(settings.BASE_DIR, 'resources', 'localization', 'schemas.json')
    with open(i18n_path, "r", encoding="utf-8") as f:
    # --- 修改结束 ---
        I18N_STRINGS = json.load(f)
except FileNotFoundError:
    print("错误: 未找到 i18n.json 翻译文件。")
    I18N_STRINGS = {}


def get_labels(lang: str = 'en') -> dict:
    """根据语言代码，获取对应的翻译标签字典"""
    return I18N_STRINGS.get(lang, I18N_STRINGS.get('en', {}))


# --- 1. 源数据 (narrative_blueprint.json) 的数据契约 ---

class Dialogue(BaseModel):
    """定义了单条对话的结构"""
    content: str
    speaker: str
    start_time: str
    end_time: str


class Highlight(BaseModel):
    """定义了高光标注的结构"""
    type: str = Field(..., alias='type_')
    description: str
    start_time: str
    end_time: str

    class Config:
        populate_by_name = True


class IdentifiedFact(BaseModel):
    """定义了单条被识别出的事实的结构，与CharacterIdentifier的输出匹配"""
    # [核心修正] 增加一个character_name字段，用于存储该事实的明确归属
    character_name: Optional[str] = Field(None, exclude=True)  # exclude=True, 因为源JSON文件中没有它
    scene_id: int
    attribute: str
    value: str
    source_text: str
    type: str
    suggested_attribute: Optional[str] = None


class Scene(BaseModel):
    """定义了单个场景的完整结构，这是我们的核心“工具箱”单元"""
    id: int
    name: str
    chapter_id: int
    dialogues: List[Dialogue] = Field(default_factory=list)
    highlights: List[Highlight] = Field(default_factory=list)
    inferred_location: Optional[str] = None
    character_dynamics: Optional[str] = None
    mood_and_atmosphere: Optional[str] = None
    enhanced_facts: List[IdentifiedFact] = Field(default_factory=list, exclude=True)

    def to_rag_a_text(self, series_id: str) -> str:
        # ... (此方法保持不变) ...
        metadata_lines = [
            "--- Metadata Block ---",
            f"series_id: {series_id}",
            f"scene_id: {self.id}",
            f"annotated_location: {self.inferred_location or 'N/A'}",
            f"annotated_mood: {self.mood_and_atmosphere or 'N/A'}",
        ]
        present_characters = list(set(d.speaker for d in self.dialogues))
        if present_characters:
            metadata_lines.append(f"present_characters: {', '.join(present_characters)}")

        if self.enhanced_facts:
            fact_summary = ", ".join([f"{fact.attribute}:{fact.value}" for fact in self.enhanced_facts])
            metadata_lines.append(f"ai_identified_facts: [{fact_summary}]")

        narrative_lines = [
            "--- Narrative Content ---"
        ]
        summary = f"本场景发生在“{self.inferred_location or '未知地点'}”，核心情节动态是“{self.character_dynamics or '未描述'}”。"
        narrative_lines.append(summary)
        narrative_lines.append("Dialogues:")
        for d in self.dialogues:
            narrative_lines.append(f"- {d.speaker}: {d.content}")

        return "\n".join(metadata_lines + narrative_lines)

    def to_rag_b_text(self, series_id: str, lang: str = 'en') -> str:
        """
        [核心修正 v1.1] 生成RAG B所需的、“事实增强版”的富文本。
        修复了事实归属逻辑，现在使用注入的 `character_name` 字段进行可靠分组。
        """
        labels = get_labels(lang)
        # --- 1. 构建元数据块 (不变) ---
        metadata_lines = [
            labels.get("metadata_block_header"),
            f"{labels.get('series_id_label')}: {series_id}",
            f"{labels.get('scene_id_label')}: {self.id}",
            f"{labels.get('location_label')}: {self.inferred_location or 'N/A'}",
            f"{labels.get('mood_label')}: {self.mood_and_atmosphere or 'N/A'}",
        ]
        present_characters = list(set(d.speaker for d in self.dialogues))
        if present_characters:
            metadata_lines.append(f"{labels.get('characters_label')}: {', '.join(present_characters)}")
        summary = self.character_dynamics or '未描述'
        metadata_lines.append(f"{labels.get('narrative_summary_label')}: {summary}")

        # --- 2. [核心重构] 构建推理事实块 ---
        inference_lines = [labels.get("inference_header")]
        if self.enhanced_facts:
            # 使用健壮的 character_name 字段进行分组，不再依赖文本匹配
            facts_by_char = defaultdict(list)
            for fact in self.enhanced_facts:
                if fact.character_name:
                    facts_by_char[fact.character_name].append(f"{fact.attribute}是“{fact.value}”")

            if facts_by_char:
                for char_name, fact_list in facts_by_char.items():
                    # 将一个角色的所有事实用逗号连接起来
                    facts_str = "，".join(fact_list) + "。"
                    inference_lines.append(f"{char_name}{labels.get('inference_summary_prefix')}: {facts_str}")

        # --- 3. 构建台词对话块 (不变) ---
        dialogue_lines = [labels.get("dialogue_header")]
        for d in self.dialogues:
            dialogue_lines.append(f"- {d.speaker}: {d.content}")

        # --- 4. 拼接所有块 (不变) ---
        final_blocks = [metadata_lines]
        if len(inference_lines) > 1:
            final_blocks.append(inference_lines)
        if len(dialogue_lines) > 1:
            final_blocks.append(dialogue_lines)

        return "\n".join(["\n".join(block) for block in final_blocks])


class ProjectMetadata(BaseModel):
    project_name: str


class NarrativeBlueprint(BaseModel):
    """定义了整个narrative_blueprint.json文件的顶层结构"""
    project_metadata: ProjectMetadata
    scenes: Dict[str, Scene]


# ... 省略其他不变的模型定义 ...
class Milestone(BaseModel):
    milestone_description: str
    related_scene_ids: List[int]


class CharacterProfile(BaseModel):
    character_name: str
    metrics: Dict[str, Any]
    reasoning_summary: Dict[str, str]
    narrative_milestones: List[Milestone]


class BrainIndex(BaseModel):
    series_id: str
    character_profiles: Dict[str, CharacterProfile]