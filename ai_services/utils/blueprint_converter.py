import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import math

# --- Django/Pydantic 核心导入 ---
from pydantic import BaseModel, Field
from django.db import models
from django.utils.translation import gettext_lazy as _


# =============================================================================
# 1. Edge Schema 核心枚举 (Enums) - 复制自用户提供的最新结构
# =============================================================================

class SceneMood(models.TextChoices):
    CALM = "Calm", _("平静")
    TENSE = "Tense", _("紧张")
    ROMANTIC = "Romantic", _("浪漫")
    JOYFUL = "Joyful", _("喜悦")
    SAD = "Sad", _("悲伤")
    MYSTERIOUS = "Mysterious", _("悬疑/神秘")
    ANGRY = "Angry", _("愤怒")
    CONFRONTATIONAL = "Confrontational", _("冲突")
    FEARFUL = "Fearful", _("恐惧")
    OPPRESSIVE = "Oppressive", _("压抑")
    EERIE = "Eerie", _("诡异")
    WARM = "Warm", _("温馨")


class SceneType(models.TextChoices):
    DIALOGUE_HEAVY = "Dialogue_Heavy", _("对话驱动")
    ACTION_DRIVEN = "Action_Driven", _("动作驱动")
    INTERNAL_MONOLOGUE = "Internal_Monologue", _("内心独白")
    VISUAL_STORYTELLING = "Visual_Storytelling", _("视觉叙事")
    TRANSITION = "Transition", _("过场")
    ESTABLISHING = "Establishing", _("铺垫/空镜")


class HighlightType(models.TextChoices):
    ACTION = "Action", _("动作片段")
    EMOTIONAL = "Emotional", _("情感片段")
    DIALOGUE = "Dialogue", _("对话片段")
    SUSPENSE = "Suspense", _("悬念片段")
    INFORMATION = "Information", _("信息片段")
    HUMOR = "Humor", _("幽默片段")
    OTHER = "Other", _("其他")


class HighlightMood(models.TextChoices):
    EXCITING = "Exciting", _("燃")
    SATISFYING = "Satisfying", _("爽")
    HEART_WRENCHING = "Heart-wrenching", _("虐")
    SWEET = "Sweet", _("甜")
    HILARIOUS = "Hilarious", _("爆笑")
    TERRIFYING = "Terrifying", _("恐怖")
    HEALING = "Healing", _("治愈")
    TOUCHING = "Touching", _("感动")
    TENSE = "Tense", _("紧张")


# =============================================================================
# 2. Edge Schema 核心数据模型 (Models) - UPDATED TO BE FLAT (修复结构)
# =============================================================================

# Context/Meta 模型 (可选，因为JSON中未出现，但最好定义以防Edge下一版加上)
class AiMetadata(BaseModel):
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    model_version: Optional[str] = None

class ItemContext(BaseModel):
    id: str = Field(..., description="UUID")
    is_verified: bool = Field(default=False)
    # 假设 DataOrigin 在实际生产中是字符串
    origin: str = Field(default="human")
    ai_meta: Optional[AiMetadata] = None


class DialogueItem(BaseModel):
    """DialogueItem 被扁平化，字段直接包含内容。"""
    start: float
    end: float
    text: str
    speaker: str = "Unknown"
    original_text: Optional[str] = None # 根据JSON样本添加
    context: Optional[ItemContext] = None # 保持可选以避免校验失败

class CaptionItem(BaseModel):
    """CaptionItem 被扁平化。"""
    start: float
    end: float
    content: str
    category: Optional[str] = None
    context: Optional[ItemContext] = None

class HighlightItem(BaseModel):
    """HighlightItem 被扁平化。"""
    start: float
    end: float
    type: HighlightType = Field(default=HighlightType.OTHER)
    mood: Optional[HighlightMood] = None
    description: Optional[str] = None
    context: Optional[ItemContext] = None

class SceneItem(BaseModel):
    """SceneItem 被扁平化，合并了 SceneContent 的字段。"""
    start: float
    end: float
    label: str
    description: Optional[str] = None
    mood: Optional[SceneMood] = None
    scene_type: Optional[SceneType] = None
    location: Optional[str] = None
    character_dynamics: Optional[str] = None
    keyframe_url: Optional[str] = None # 根据旧版结构保留，但在新JSON中缺失，仍设为Optional
    context: Optional[ItemContext] = None # 保持可选以避免校验失败


# =============================================================================
# 2. Edge Schema 核心数据模型 (Models) - UPDATED TO MATCH CLEANED JSON STRUCTURE
#    注意：我们只定义用于解析 Blueprint 最终交付物（Chapter）的模型。
#    Chapter.scenes/dialogues 等字段是 List[Dict[str, Any]] (扁平字典)，
#    因此我们只需确保 Blueprint/Chapter 自身的结构正确。
# =============================================================================

class Chapter(BaseModel):
    """
    [章节] (Consumer Unit)
    - 适配新增的 sequence_number 字段。
    - 保持 List[Dict[str, Any]] 结构来接收 Edge 侧清洗后的扁平化数据。
    """
    id: str = Field(..., description="章节ID (MediaID)")
    # [关键修复] 使用新增的 sequence_number 字段
    sequence_number: int = Field(..., description="叙事顺序，用于 VSS Cloud 排序和逻辑处理")
    name: str = Field(..., description="章节名称")
    source_file: str = Field(..., description="关联视频路径")
    duration: float

    # 接收 Edge 侧清洗后的扁平化字典列表
    scenes: List[Dict[str, Any]]
    dialogues: List[Dict[str, Any]]
    captions: List[Dict[str, Any]]
    highlights: List[Dict[str, Any]]


class Blueprint(BaseModel):
    """
    [蓝图] (Delivery Artifact)
    """
    project_id: str
    asset_id: str
    project_name: str
    global_character_list: List[str] = Field(default_factory=list)
    chapters: Dict[str, Chapter] = {}


# =============================================================================
# 3. BlueprintConverter - 转换工具核心逻辑
# =============================================================================

class BlueprintConverter:

    def __init__(self):
        self.global_scene_counter = 0
        self.old_scenes = {}
        self.old_chapters = {}
        self.old_narrative_timeline = {"type": "linear", "sequence": {}}
        self.scene_id_to_chapter_id_map: Dict[int, str] = {}

    @staticmethod
    def _seconds_to_time_str(seconds: float) -> str:
        """ 转换秒数浮点数到 HH:MM:SS.mmm 字符串格式。"""
        if seconds < 0:
            seconds = 0

        seconds = round(seconds, 3)
        td = timedelta(seconds=seconds)

        total_seconds = td.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        sec = int(total_seconds % 60)

        milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))

        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{milliseconds:03d}"

    # 移除 _get_enum_value 方法，因为现在直接处理字典中的字符串值。

    def convert(self, new_blueprint: Blueprint) -> Dict[str, Any]:
        """
        主入口：执行完整转换流程。
        """
        # 重置内部状态
        self.global_scene_counter = 0
        self.old_scenes = {}
        self.old_chapters = {}
        self.old_narrative_timeline = {"type": "linear", "sequence": {}}

        # 1. 转换 Chapters：使用 sequence_number 确保处理顺序
        # 将 chapters 字典转换为列表，并按 sequence_number 字段排序
        sorted_chapters = sorted(
            new_blueprint.chapters.values(),
            key=lambda c: c.sequence_number
        )

        for chapter_obj in sorted_chapters:
            # [修改] 使用 chapter_obj.sequence_number 作为 chapter_index (整数)
            self._process_chapter(
                chapter_obj.id,
                chapter_obj,
                chapter_obj.sequence_number
            )

        # 2. 组装最终输出
        old_output = {
            "project_metadata": {
                "project_name": new_blueprint.project_name,
                "project_id": new_blueprint.project_id,
                "asset_id": new_blueprint.asset_id,
                "total_chapters": len(self.old_chapters),
                "total_scenes": self.global_scene_counter,
                "version": "1.0-temp-compat",
                "generation_date": datetime.now().isoformat(),
            },
            "chapters": self.old_chapters,
            "scenes": self.old_scenes,
            "narrative_timeline": self.old_narrative_timeline,
        }

        return old_output

    # [新增方法] 暴露映射表
    def get_scene_chapter_map(self) -> Dict[str, str]:
        """返回临时 Scene ID (str) 到原始 Chapter ID (str/UUID) 的映射。"""
        # 将整数键转换为字符串，以匹配剪辑脚本中的数据类型
        return {str(k): v for k, v in self.scene_id_to_chapter_id_map.items()}

    def _process_chapter(self, chapter_id: str, chapter_obj: Chapter, chapter_index: int):
        """
        处理单个 Chapter 的数据重组、ID 生成和 Timeline 模拟。
        chapter_index 现在是语义明确的 sequence_number (整数)。
        """
        # 1. 构造旧版 Chapter 结构
        old_chapter_data = {
            # Chapter ID 现在可以是一个 UUID 字符串
            "id": chapter_id,
            "name": chapter_obj.name,
            "textual": f"Chapter {chapter_index} - {chapter_obj.name}",
            "source_file": chapter_obj.source_file,
            "scene_ids": []
        }

        # 2. 预处理并行事件：List[Dict[str, Any]]
        events_by_time_slot = []

        # item_dict 现在是字典，直接访问键
        for d in chapter_obj.dialogues:
            events_by_time_slot.append((d['start'], d['end'], 'dialogue', d))
        for c in chapter_obj.captions:
            events_by_time_slot.append((c['start'], c['end'], 'caption', c))
        for h in chapter_obj.highlights:
            events_by_time_slot.append((h['start'], h['end'], 'highlight', h))

        # 3. 遍历 SceneItems
        for scene_item_dict in chapter_obj.scenes:
            # 3.1 生成全局 ID
            self.global_scene_counter += 1
            global_scene_id = self.global_scene_counter

            # 3.2 初始化嵌套容器
            new_dialogues = []
            new_captions = []
            new_highlights = []

            scene_start = scene_item_dict['start']
            scene_end = scene_item_dict['end']

            # 3.3 时间关联和嵌套事件 (现在直接使用字典访问)
            for event_start, event_end, event_type, item_dict in events_by_time_slot:
                # 宽松匹配：事件在场景内
                if event_start >= scene_start and event_end <= scene_end:
                    start_time_str = self._seconds_to_time_str(event_start)
                    end_time_str = self._seconds_to_time_str(event_end)

                    if event_type == 'dialogue':
                        new_dialogues.append({
                            "content": item_dict['text'],
                            "speaker": item_dict['speaker'],
                            "start_time": start_time_str,
                            "end_time": end_time_str,
                        })

                    elif event_type == 'highlight':
                        new_highlights.append({
                            "id": len(new_highlights) + 1,
                            "type": item_dict.get('type') or 'Other',
                            "description": item_dict.get('description') or '',
                            "mood": item_dict.get('mood') or 'N/A',
                            "start_time": start_time_str,
                            "end_time": end_time_str,
                        })

                    elif event_type == 'caption':
                        new_captions.append({
                            "content": item_dict['content'],
                            "start_time": start_time_str,
                            "end_time": end_time_str,
                        })

            # 3.4 构造旧版 Scene 对象
            old_scene = {
                "id": global_scene_id,
                "name": scene_item_dict['label'],
                "textual": scene_item_dict['label'],
                "chapter_id": chapter_index,  # [关键修复] 使用 sequence_number (整数)
                "dialogues": new_dialogues,
                "captions": new_captions,
                "highlights": new_highlights,

                # 提取 SceneContent 字段 (直接从字典获取)
                "inferred_location": scene_item_dict.get('location') or 'N/A',
                "mood_and_atmosphere": scene_item_dict.get('mood') or 'N/A',
                "character_dynamics": scene_item_dict.get('character_dynamics') or '',
                "scene_content_type": scene_item_dict.get('scene_type') or 'N/A',

                # 转换时间格式
                "start_time": self._seconds_to_time_str(scene_start),
                "end_time": self._seconds_to_time_str(scene_end),

                # 默认填充旧版所需但新版缺失的字段
                "branch": {"id": 0, "type": "linear", "intersection_with": []},
                "timeline_marker": {"type": "NONE"}
            }

            # 3.5 存储到全局字典
            self.old_scenes[str(global_scene_id)] = old_scene
            old_chapter_data["scene_ids"].append(global_scene_id)

            # [关键新增] 存储映射：全局 Scene ID (int) 映射到原始 Chapter ID (UUID)
            self.scene_id_to_chapter_id_map[global_scene_id] = chapter_id

            # 3.6 模拟 narrative_timeline
            self.old_narrative_timeline["sequence"][str(global_scene_id)] = {
                "narrative_index": global_scene_id
            }

        # 4. 存储处理好的 Chapter
        self.old_chapters[chapter_id] = old_chapter_data