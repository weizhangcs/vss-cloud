# 文件路径: ai_services/rag/schemas.py
# 描述: [重构后] 定义了RAG部署流程中使用到的核心数据结构（数据契约）。
#      已与Django框架解耦，i18n翻译文件路径由外部动态加载。
# 版本: 2.0 (Decoupled & Reviewed)

import json
from collections import defaultdict
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from pathlib import Path

# 定义一个全局变量，用于缓存加载后的i18n翻译字符串。
# 这样做可以避免在每次调用get_labels时都重复读取文件。
I18N_STRINGS: Dict = {}

def load_i18n_strings(i18n_path: Path):
    """
    从指定路径加载i18n翻译文件到全局缓存。

    这个函数应该在应用启动时或在Celery任务开始时被调用一次，
    以确保 Pydantic 模型在解析和生成文本时能够访问到翻译内容。

    Args:
        i18n_path (Path): 指向 i18n JSON 文件的完整 Path 对象。
    """
    global I18N_STRINGS
    # 只有在缓存为空时才执行文件读取，避免重复加载。
    if not I18N_STRINGS:
        try:
            with i18n_path.open("r", encoding="utf-8") as f:
                I18N_STRINGS = json.load(f)
        except FileNotFoundError:
            # 如果文件未找到，打印警告并设置为空字典，以防程序崩溃。
            print(f"警告: 未找到 i18n 翻译文件于路径: {i18n_path}。")
            I18N_STRINGS = {}
        except json.JSONDecodeError:
            print(f"警告: 解析 i18n 翻译文件失败于路径: {i18n_path}。")
            I18N_STRINGS = {}

def get_labels(lang: str = 'en') -> dict:
    """
    根据语言代码，从全局缓存中获取对应的翻译标签字典。

    Args:
        lang (str): 目标语言代码 (e.g., 'zh', 'en')。

    Returns:
        dict: 包含该语言所有翻译标签的字典。如果找不到，则回退到英文。
    """
    # 提供一个回退机制，如果指定语言不存在，则尝试使用英文。
    return I18N_STRINGS.get(lang, I18N_STRINGS.get('en', {}))


# --- Pydantic 模型定义 ---
# 以下所有数据模型的定义保持不变，因为它们的结构和逻辑是正确的。
# 它们现在依赖于通过 load_i18n_strings 函数填充的全局 I18N_STRINGS 变量。

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
    character_name: Optional[str] = Field(None, exclude=True)
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

    def to_rag_text(self, asset_id: str, lang: str = 'en') -> str:
        """
        [修正后] 生成RAG引擎所需的、“事实增强版”的富文本文档。
        采用更安全的 f-string 写法以兼容 Python 3.11。
        """
        labels = get_labels(lang)
        # --- 1. 构建元数据块 ---
        metadata_lines = [
            labels.get("metadata_block_header", "--- Metadata Block ---"),
            f"{labels.get('asset_id_label', 'Asset ID')}: {asset_id}",
            f"{labels.get('scene_id_label', 'Scene ID')}: {self.id}",
            f"{labels.get('location_label', 'Location')}: {self.inferred_location or 'N/A'}",
            f"{labels.get('mood_label', 'Mood')}: {self.mood_and_atmosphere or 'N/A'}",
        ]
        present_characters = list(set(d.speaker for d in self.dialogues))
        if present_characters:
            characters_label = labels.get('characters_label', 'Present Characters')
            metadata_lines.append(f"{characters_label}: {', '.join(present_characters)}")

        summary_label = labels.get('narrative_summary_label', 'The core narrative of this scene is')
        summary = self.character_dynamics or '未描述'
        metadata_lines.append(f"{summary_label}: {summary}")

        # --- 2. 构建推理事实块 ---
        inference_lines = [labels.get("inference_header", "--- Inferred Facts ---")]
        if self.enhanced_facts:
            facts_by_char = defaultdict(list)
            for fact in self.enhanced_facts:
                if fact.character_name:
                    # 将 f-string 格式化移到 append 调用之外，保持简洁
                    fact_text = f"{fact.attribute}是“{fact.value}”"
                    facts_by_char[fact.character_name].append(fact_text)

            if facts_by_char:
                # [核心修正] 将标签获取和字符串格式化分开，避免复杂的 f-string
                prefix_label = labels.get('inference_summary_prefix', "'s inferred facts")
                for char_name, fact_list in facts_by_char.items():
                    facts_str = "，".join(fact_list) + "。"
                    # 使用预先获取的标签来构建最终的字符串
                    inference_line = f"{char_name}{prefix_label}: {facts_str}"
                    inference_lines.append(inference_line)

        # --- 3. 构建台词对话块 ---
        dialogue_lines = [labels.get("dialogue_header", "--- Dialogues ---")]
        for d in self.dialogues:
            dialogue_lines.append(f"- {d.speaker}: {d.content}")

        # --- 4. 拼接所有块 ---
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