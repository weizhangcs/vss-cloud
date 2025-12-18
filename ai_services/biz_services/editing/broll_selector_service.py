# 文件路径: ai_services/editing/broll_selector_service.py
# 描述: [重构后] B-Roll选择器服务，已完全解耦。
# 版本: 4.0 (Decoupled & Integrated)
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Core / Platform
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from core.exceptions import BizException, RateLimitException
from core.error_codes import ErrorCode
from pydantic import ValidationError

# Schemas / Models
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from .schemas import BrollSelectionLLMResponse, EditingServiceParams, EditingResult, EditingSequence, BrollClip


class BrollSelectorService:
    """
    [Service] B-Roll 选择器 (V6 Adapted).
    直接消费 NarrativeDataset 对象。
    """
    SERVICE_NAME = "broll_selector_service"

    def __init__(self,
                 prompts_dir: Path,
                 logger: logging.Logger,
                 work_dir: Path,
                 localization_path: Path,
                 gemini_processor: GeminiProcessor):
        self.logger = logger
        self.work_dir = work_dir
        self.prompts_dir = prompts_dir
        self.localization_path = localization_path
        self.gemini_processor = gemini_processor

        self.labels = {}

    def _load_localization_file(self, path: Path, lang: str):
        try:
            if path.exists():
                with path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.labels = data.get(lang, data.get('en', {}))
            else:
                self.logger.warning(f"Localization file not found: {path}")
        except Exception as e:
            self.logger.warning(f"Failed to load localization: {e}")

    # ... (辅助方法 _time_str_to_seconds, _seconds_to_time_str 保持不变) ...
    @staticmethod
    def _time_str_to_seconds(time_str: str) -> float:
        try:
            h, m, s = time_str.split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
        except:
            return 0.0

    @staticmethod
    def _seconds_to_time_str(seconds: float) -> str:
        if seconds < 0: seconds = 0
        td = timedelta(seconds=seconds)
        minutes, sec = divmod(td.seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{sec + td.microseconds / 1e6:06.3f}"

    def execute(self,
                dubbing_data: Dict[str, Any],
                dataset: NarrativeDataset,
                config: EditingServiceParams) -> Dict[str, Any]:

        self.logger.info(f"Starting B-Roll Selection (Lang: {config.default_lang})...")

        # 1. 加载 UI 标签
        self._load_localization_file(self.localization_path, config.default_lang)

        # 2. 准备 Scene Map
        # key: str(scene_id), value: Scene Object
        scenes_map = dataset.scenes

        # [Fix] 构建 Scene -> Chapter 的反向查找表
        # NarrativeDataset V6 中，Chapter 包含 scene_ids，但 Scene 不包含 chapter_id
        scene_to_chapter_map = {}
        if dataset.chapters:
            # 假设 dataset.chapters 是一个列表或字典
            chapters_iter = dataset.chapters.values() if isinstance(dataset.chapters, dict) else dataset.chapters

            for chapter in chapters_iter:
                # 获取 Chapter UUID (兼容可能的命名差异)
                c_uuid = getattr(chapter, "chapter_uuid", getattr(chapter, "uuid", None))
                # 获取 Scene IDs
                s_ids = getattr(chapter, "scene_ids", [])

                if c_uuid:
                    for s_id in s_ids:
                        scene_to_chapter_map[str(s_id)] = c_uuid

        final_sequences: List[EditingSequence] = []
        input_script = dubbing_data.get("dubbing_script", [])
        total_items = len(input_script)

        for i, entry in enumerate(input_script):
            self.logger.info(f"Processing sequence {i + 1}/{total_items}...")

            narration_text = entry.get("narration", "")
            target_duration = entry.get("duration_seconds", 0.0)
            audio_path = entry.get("audio_file_path")
            source_scene_ids = entry.get("source_scene_ids", [])

            if not target_duration or not source_scene_ids:
                self.logger.warning(f"Skipping seq {i}: missing duration/scenes.")
                continue

            # 3. 构建候选池
            candidate_pool = self._build_candidate_pool(source_scene_ids, scenes_map, config.gap_threshold)

            if not candidate_pool:
                self.logger.warning(f"Skipping seq {i}: no candidates.")
                final_sequences.append(EditingSequence(
                    narration=narration_text,
                    narration_duration=target_duration,
                    narration_audio_path=audio_path,
                    b_roll_clips=[]
                ))
                continue

            # 4. LLM 选择
            selected_clips_data = self._select_sequence_via_llm(
                narration=narration_text,
                duration=target_duration,
                pool=candidate_pool,
                config=config
            )

            # 5. 注入 Chapter ID (关键步骤：Edge端需要这个UUID)
            b_roll_clips_objs = []
            for clip_data in selected_clips_data:
                sid_str = str(clip_data['scene_id'])

                # [Fix] 使用反向查找表获取 Chapter UUID
                #chapter_uuid = scene_to_chapter_map.get(sid_str)
                # 获取 UUID 对象
                raw_uuid = scene_to_chapter_map.get(sid_str)
                chapter_uuid_str = str(raw_uuid) if raw_uuid else None

                if not raw_uuid:
                    self.logger.warning(f"Scene {sid_str} does not belong to any chapter. Chapter ID will be None.")

                # 构造 Pydantic 对象
                clip_obj = BrollClip(
                    **clip_data,
                    chapter_id=chapter_uuid_str  # 传入字符串
                )
                b_roll_clips_objs.append(clip_obj)

            # 6. 添加结果
            final_sequences.append(EditingSequence(
                narration=narration_text,
                narration_duration=target_duration,
                narration_audio_path=audio_path,
                b_roll_clips=b_roll_clips_objs
            ))

        # 7. 封装最终结果
        result = EditingResult(
            generation_date=dubbing_data.get("generation_date"),
            asset_name=dubbing_data.get("asset_name"),
            editing_script=final_sequences,
            total_sequences=len(final_sequences)
        )

        return result.model_dump()

    def _build_candidate_pool(self, scene_ids: List[int], scenes_map: Dict, gap_threshold: float) -> List[Dict]:
        """
        构建候选池 (适配 V6 Dataset Object)
        """
        pool = []
        # scene_ids 是 [101, 102]
        for sid in sorted(scene_ids):
            sid_str = str(sid)
            scene = scenes_map.get(sid_str)
            if not scene: continue

            # V6 Dataset: scene.dialogues 是 List[DialogueItem]
            # 我们需要转成 dict list 或者直接操作 object
            # 为了复用旧逻辑方便，这里做个简单的转换
            dialogues = [d.model_dump() for d in scene.dialogues] if scene.dialogues else []
            if not dialogues: continue

            # ... (原来的分组逻辑，复用即可) ...
            # 这里为节省篇幅略去分组算法细节，假设已复用 _format_clip_group
            # 最终返回 list of dict

            # [Copied Logic from original file]
            current_group = []
            scene_clips = []
            for dialogue in dialogues:
                if not current_group:
                    current_group.append(dialogue)
                else:
                    prev_end = self._time_str_to_seconds(current_group[-1]['end_time'])
                    curr_start = self._time_str_to_seconds(dialogue['start_time'])
                    if curr_start - prev_end < gap_threshold:
                        current_group.append(dialogue)
                    else:
                        scene_clips.append(self._format_clip_group(current_group, sid))
                        current_group = [dialogue]
            if current_group:
                scene_clips.append(self._format_clip_group(current_group, sid))
            pool.extend(scene_clips)

        return pool

    def _format_clip_group(self, group: List[Dict], scene_id: int) -> Dict:
        """
        辅助函数：将一个对话组格式化为最终的clip对象。
        scene_id现在被直接传入，不再需要反查。
        """
        start_time = group[0]['start_time']
        end_time = group[-1]['end_time']
        duration = self._time_str_to_seconds(end_time) - self._time_str_to_seconds(start_time)

        return {
            "type": "dialogue_group" if len(group) > 1 else "dialogue_single",
            "is_group": len(group) > 1,
            "scene_id": scene_id,
            "content": "\n".join([f"{d['speaker']}: {d['content']}" for d in group]),
            "start_time": start_time,
            "end_time": end_time,
            "duration": round(duration, 3)
        }

    def _select_sequence_via_llm(self,
                                 narration: str,
                                 duration: float,
                                 pool: List[Dict],
                                 config: EditingServiceParams) -> List[Dict]:
        """
        [Core Logic] LLM 选择 + 时长自适应微调算法
        """
        # 1. 准备富文本列表 (供 LLM 阅读)
        # 这一步将结构化数据转为 Prompt 友好的文本
        rich_list_str = self._build_rich_text(pool, config.default_lang)

        # 2. 构建 Prompt
        # 加载对应的语言模板 (e.g., broll_sequence_selector_en.txt)
        prompt_template = self._load_prompt_template(config.default_lang)
        prompt = prompt_template.format(
            narration=narration,
            target_duration=duration,
            rich_candidate_list=rich_list_str
        )

        # 3. LLM 推理
        try:
            # 使用 config 中的模型配置
            resp_data, _ = self.gemini_processor.generate_content(
                model_name=config.default_model,
                prompt=prompt,
                temperature=0.1  # 保持低温，确保 ID 选择准确
            )

            # 4. Schema 校验 (Pydantic)
            # 确保 LLM 返回的是 {"selected_ids": [...]} 格式
            validated = BrollSelectionLLMResponse(**resp_data)

            # 5. ID 解析与映射
            # 假设 LLM 返回 ["ID-0", "ID-2"]
            selected_indices = []
            for sid in validated.selected_ids:
                try:
                    # 容错处理：提取数字 ID
                    idx = int(sid.replace("ID-", "").strip())
                    selected_indices.append(idx)
                except ValueError:
                    self.logger.warning(f"Invalid ID format from LLM: {sid}")
                    continue

            # 映射回对象池 (Crucial: Use Copy)
            # 必须复制对象，因为后续的微调算法会修改 duration/end_time
            # 如果不复制，会污染原始 candidate_pool，影响后续复用
            selected_clips = []
            for i in selected_indices:
                if 0 <= i < len(pool):
                    selected_clips.append(pool[i].copy())
                else:
                    self.logger.warning(f"LLM returned out-of-bound index: {i}")

        except (ValidationError, Exception) as e:
            self.logger.error(f"LLM Selection/Validation failed: {e}")
            # 降级策略：如果 LLM 失败，返回空列表，由上层决定是否兜底
            return []

        if not selected_clips:
            return []

        # =========================================================
        # 6. 时长微调算法 (Time Adjustment Algorithm)
        # =========================================================
        # 这是为了解决 LLM 选出的片段总时长与配音时长不完全匹配的问题

        actual_duration = sum(c['duration'] for c in selected_clips)
        duration_delta = actual_duration - duration
        min_clip_duration = 0.5  # 最小保留时长 0.5s，防止裁剪成 0

        # Case A: 选多了 (Overshoot) -> 需要裁剪
        if duration_delta > 0.1:
            overshoot_to_trim = duration_delta

            # 策略：倒序裁剪 (从最后一个片段开始剪)，因为结尾通常不如开头重要
            for clip in reversed(selected_clips):
                if overshoot_to_trim <= 0:
                    break

                current_dur = clip['duration']
                # 计算该片段最多能剪多少 (必须保留 min_clip_duration)
                max_trimmable = current_dur - min_clip_duration

                if max_trimmable <= 0:
                    continue

                # 实际裁剪量
                trim_amount = min(overshoot_to_trim, max_trimmable)

                # 执行修改
                new_duration = current_dur - trim_amount
                clip['original_duration'] = current_dur  # 备份原始时长
                clip['duration'] = round(new_duration, 3)

                # 重新计算 end_time 字符串 (HH:MM:SS.mmm)
                start_sec = self._time_str_to_seconds(clip['start_time'])
                new_end_sec = start_sec + new_duration
                clip['end_time'] = self._seconds_to_time_str(new_end_sec)

                # 更新剩余需要裁剪的量
                overshoot_to_trim -= trim_amount

        # Case B: 选少了 (Undershoot) -> 需要延长
        elif duration_delta < -0.1:
            # 策略：延长最后一个片段 (Extend last clip)
            # 注意：这里只是在数据层面延长 duration。
            # 实际渲染时，如果素材本身不够长，通常会采用 Freeze Frame (定格) 或 Slow Motion

            last_clip = selected_clips[-1]

            # 备份原始时长 (如果还没有备份过)
            if 'original_duration' not in last_clip:
                last_clip['original_duration'] = last_clip['duration']

            missing_seconds = abs(duration_delta)

            # 执行修改
            new_duration = last_clip['duration'] + missing_seconds
            last_clip['duration'] = round(new_duration, 3)

            # 重新计算 end_time
            start_sec = self._time_str_to_seconds(last_clip['start_time'])
            new_end_sec = start_sec + new_duration
            last_clip['end_time'] = self._seconds_to_time_str(new_end_sec)

        return selected_clips

    def _load_prompt_template(self, lang: str) -> str:
        """
        [Helper] 加载 B-Roll 选择器的 Prompt 模板。
        支持语言回退机制 (Target Lang -> ZH -> Error)。
        """
        # 1. 尝试加载目标语言模板
        # 文件名约定: broll_sequence_selector_{lang}.txt
        target_path = self.prompts_dir / f"broll_sequence_selector_{lang}.txt"

        if target_path.exists():
            return target_path.read_text(encoding='utf-8')

        # 2. 回退到中文 (作为默认的基础模板)
        fallback_path = self.prompts_dir / "broll_sequence_selector_zh.txt"
        if fallback_path.exists():
            self.logger.info(f"Prompt template for '{lang}' not found. Falling back to 'zh'.")
            return fallback_path.read_text(encoding='utf-8')

        # 3. 如果连中文模板都没有，这是一个配置错误
        raise FileNotFoundError(f"Prompt templates not found in {self.prompts_dir}. Checked '{lang}' and 'zh'.")

    def _build_rich_text(self, candidate_pool: List[Dict], lang: str) -> str:
        """
        [Helper] 构建提供给 LLM 阅读的富文本素材列表。

        特性:
        1. 使用 self.labels 中的本地化标签 (e.g. "连贯对话" vs "Coherent Dialogue")。
        2. 将多行对话内容扁平化为单行 (用 " | " 分隔)，方便 Token 节省和 LLM 解析。
        3. 保留 ID 索引，供 LLM 引用。
        """
        # 1. 获取 UI 标签 (self.labels 已经在 execute -> _load_localization_file 中加载完毕)
        # 提供英文硬编码作为最后的兜底
        lbl_no_cand = self.labels.get('no_candidates', '(No candidate clips)')
        lbl_type_group = self.labels.get('clip_type_group', 'Coherent Dialogue')
        lbl_type_single = self.labels.get('clip_type_single', 'Single Dialogue')
        lbl_duration = self.labels.get('duration_label', 'Duration')
        lbl_summary = self.labels.get('content_summary_label', 'Content Summary')

        # 2. 处理空池情况
        if not candidate_pool:
            return lbl_no_cand

        lines = []
        for i, clip in enumerate(candidate_pool):
            # 3. 判断素材类型 (对话组 vs 单句)
            clip_type = lbl_type_group if clip.get('is_group') else lbl_type_single

            # 4. 格式化内容摘要
            # 核心逻辑: 将换行符替换为 " | " 分隔符，保留所有对话内容，同时保持单行格式
            raw_content = clip.get('content', 'N/A')
            # 确保 content 是字符串
            if not isinstance(raw_content, str):
                raw_content = str(raw_content)
            content_summary = raw_content.replace('\n', ' | ')

            # 5. 组装单行描述
            # 格式: ID-0: [连贯对话] 时长: 5.2s, 内容摘要: A: 你好 | B: 你好
            line = (
                f"ID-{i}: [{clip_type}] "
                f"{lbl_duration}: {clip.get('duration')}s, "
                f"{lbl_summary}: {content_summary}"
            )
            lines.append(line)

        return "\n".join(lines)