# ai_services/ai_platform/rag/schemas.py

import json
from collections import defaultdict
from typing import Dict, List, Optional
from pathlib import Path
from pydantic import BaseModel, Field

from ai_services.biz_services.narrative_dataset import NarrativeScene

# --- 1. RAG 专用数据结构 ---

class IdentifiedFact(BaseModel):
    """
    定义 RAG 接收的事实结构。
    必须与 CharacterIdentifier 的输出兼容，但在这里我们可能需要更宽松的校验
    或者对数据进行清洗。
    """
    character_name: Optional[str] = Field(None, description="事实归属的角色")
    scene_id: int
    attribute: str
    value: str
    source_text: Optional[str] = None
    type: str = "general"

    # 允许额外字段
    class Config:
        extra = "ignore"

        # 确保 value 是字符串 (防御性编程)

    def __init__(self, **data):
        if 'value' in data:
            data['value'] = str(data['value'])
        super().__init__(**data)

class CharacterFactsFile(BaseModel):
    generation_date: Optional[str] = None
    source_file: Optional[str] = None
    identified_facts_by_character: Dict[str, List[Dict]] = Field(default_factory=dict)


# --- 3. [核心新增] 任务输入契约 (Task Payload Schema) ---

class RagTaskPayload(BaseModel):
    """
    RAG 部署任务的完整 Payload 契约
    对应 task.payload 字段
    """
    # 必需: 剧本蓝图路径 (V6 Dataset)
    absolute_blueprint_input_path: str = Field(..., description="V6 NarrativeDataset 输入文件的绝对路径")

    # 必需: 事实文件路径 (Character Identifier Output)
    absolute_facts_input_path: str = Field(..., description="人物事实文件的绝对路径")

    # 可选: 明确指定 Asset ID (如果 dataset 中有，则以 dataset 为准，这里作为覆盖或备用)
    asset_id: Optional[str] = Field(None, description="媒资 ID")

    # 兼容性字段 (处理旧代码可能的传参)
    absolute_input_file_path: Optional[str] = Field(None, description="兼容旧版本的蓝图路径 key")

    lang: str = Field(default="zh", description="生成文档的目标语言 (zh/en)")

# --- 4. 内容格式化器 (Adapter) ---

class RagContentFormatter:
    """
    负责将 NarrativeScene (V6) 和 IdentifiedFacts 转换为 RAG 友好的富文本。
    """

    @staticmethod
    def format_scene(scene: NarrativeScene,
                     facts: List[IdentifiedFact],
                     asset_id: str,
                     labels: Dict[str, str]) -> str:

        """
        生成单场景的 RAG 文档内容。
        """

        # --- A. 元数据块 (Metadata Block) ---
        metadata_lines = [
            labels.get("metadata_block_header", "--- 元数据块 ---"),
            f"{labels.get('asset_id_label', '媒资ID')}: {asset_id}",
            f"{labels.get('scene_id_label', '场景ID')}: {scene.local_id}",  # V6 使用 local_id
            f"{labels.get('location_label', '地点')}: {scene.inferred_location or 'N/A'}",
            f"{labels.get('mood_label', '氛围')}: {scene.mood_and_atmosphere or 'N/A'}",
        ]

        # 提取出场角色
        present_characters = list(set(d.speaker for d in scene.dialogues if d.speaker))
        if present_characters:
            char_label = labels.get('characters_label', '出场角色')
            metadata_lines.append(f"{char_label}: {', '.join(present_characters)}")

        # 核心叙事 (Character Dynamics 通常承载了动作描述)
        summary_label = labels.get('narrative_summary_label', '本场景的核心叙事是')
        summary = scene.character_dynamics or '未描述'
        metadata_lines.append(f"{summary_label}: {summary}")

        # --- B. 推理事实块 (Inferred Facts Block) ---
        inference_lines = [labels.get("inference_header", "---推理事实---")]
        if facts:
            facts_by_char = defaultdict(list)
            for fact in facts:
                if fact.character_name:
                    # e.g. "职业是植物学家"
                    facts_by_char[fact.character_name].append(f"{fact.attribute}是“{fact.value}”")

            if facts_by_char:
                prefix_label = labels.get('inference_summary_prefix', "的推理事实")
                for char_name, fact_list in facts_by_char.items():
                    # e.g. "张强的推理事实: 职业是...，性格是...。"
                    facts_str = "，".join(fact_list) + "。"
                    inference_lines.append(f"{char_name}{prefix_label}: {facts_str}")

        # --- C. 台词对话块 (Dialogues Block) ---
        dialogue_lines = [labels.get("dialogue_header", "---台词对话 ---")]
        for d in scene.dialogues:
            dialogue_lines.append(f"- {d.speaker}: {d.content}")

        # --- D. 拼装 ---
        final_blocks = [metadata_lines]

        # 只有当有内容时才添加块，避免产生空标题
        if len(inference_lines) > 1:
            final_blocks.append(inference_lines)

        # 对话通常都有，但也防御一下
        if len(dialogue_lines) > 1:
            final_blocks.append(dialogue_lines)

        return "\n".join(["\n".join(block) for block in final_blocks])