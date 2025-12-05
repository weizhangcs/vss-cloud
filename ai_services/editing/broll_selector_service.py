# 文件路径: ai_services/editing/broll_selector_service.py
# 描述: [重构后] B-Roll选择器服务，已完全解耦。
# 版本: 4.0 (Decoupled & Integrated)

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List

# 导入项目内部依赖
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor

from core.exceptions import BizException, RateLimitException
from core.error_codes import ErrorCode
from pydantic import ValidationError
from .schemas import BrollSelectionResponse

class BrollSelectorService(AIServiceMixin):
    """
    [重构后] B-Roll选择器服务。

    根据输入的解说词和时长，从剧本蓝图中挑选最合适的对话片段，
    生成一份结构化的剪辑脚本。本服务已解耦，所有依赖通过构造函数注入。
    """
    SERVICE_NAME = "broll_selector_service"

    def __init__(self,
                 prompts_dir: Path,
                 logger: logging.Logger,
                 work_dir: Path,
                 localization_path: Path,
                 gemini_processor: GeminiProcessor):
        """
        初始化B-Roll选择器服务。

        Args:
            prompts_dir (Path): 包含此服务所需prompt模板的目录路径。
            logger (logging.Logger): 一个已配置好的日志记录器实例。
            work_dir (Path): 服务的工作目录，用于存储调试文件等。
            gemini_processor (GeminiProcessor): AI通信处理器实例。
        """
        # 核心依赖
        self.logger = logger
        self.work_dir = work_dir
        self.prompts_dir = prompts_dir
        self.localization_path = localization_path
        self.gemini_processor = gemini_processor

        self.labels = {}  # 由 AIServiceMixin 使用
        self.logger.info("BrollSelectorService initialized (decoupled).")

    # _time_str_to_seconds 和 _seconds_to_time_str 是纯静态方法，保持不变
    @staticmethod
    def _time_str_to_seconds(time_str: str) -> float:
        # ... (逻辑不变) ...
        try:
            h, m, s = time_str.split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
        except:
            return 0.0

    @staticmethod
    def _seconds_to_time_str(seconds: float) -> str:
        # ... (逻辑不变) ...
        if seconds < 0: seconds = 0
        td = timedelta(seconds=seconds)
        minutes, sec = divmod(td.seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{sec + td.microseconds / 1e6:06.3f}"

    def execute(self, dubbing_path: Path, blueprint_path: Path, **kwargs) -> Dict[str, Any]:
        """
        执行B-roll选择的核心逻辑。

        Args:
            dubbing_path (Path): 指向配音脚本JSON文件的路径。
            blueprint_path (Path): 指向剧本蓝图JSON文件的路径。
            **kwargs: 其他可选参数 (如 model, lang)。

        Returns:
            Dict[str, Any]: 包含最终剪辑脚本的字典。
        """
        # [新增] 优先解析语言配置
        # 优先级: API请求参数 > YAML配置 > 代码硬编码
        current_lang = kwargs.get('lang', kwargs.get('default_lang', 'en'))
        # [新增] 2. 加载特定语言的 UI 标签
        self._load_localization_file(self.localization_path, current_lang)

        with dubbing_path.open('r', encoding='utf-8') as f:
            dubbing_data = json.load(f).get("dubbing_script", [])
        with blueprint_path.open('r', encoding='utf-8') as f:
            scenes_map = json.load(f).get("scenes", {})

        final_script = []
        total_sequences = len(dubbing_data)

        # [核心修改] 移除了 tqdm 包装器
        for i, entry in enumerate(dubbing_data):
            # [推荐新增] 使用 logger 记录进度，这在后台任务中是最佳实践
            self.logger.info(f"Processing narration sequence {i + 1}/{total_sequences}...")

            target_duration = entry.get("duration_seconds")
            source_scene_ids = entry.get("source_scene_ids", [])
            if not target_duration or not source_scene_ids:
                self.logger.warning(f"Skipping sequence {i + 1} due to missing duration or scene IDs.")
                continue

            candidate_pool = self._build_coherent_candidate_pool(source_scene_ids, scenes_map)
            if not candidate_pool:
                self.logger.warning(f"Skipping sequence {i + 1} as no candidate clips could be built.")
                continue

            # 为了确保覆盖默认值，我们更新 kwargs 或者显式传递
            call_kwargs = kwargs.copy()
            call_kwargs['lang'] = current_lang

            clip_sequence = self._select_and_adjust_sequence(entry["narration"], target_duration, candidate_pool,
                                                             **call_kwargs)

            final_script.append({
                "narration": entry["narration"], "narration_duration": target_duration,
                "narration_audio_path": entry.get("audio_file_path"), "b_roll_clips": clip_sequence
            })

        final_output = {"editing_script": final_script}
        self.logger.info("B-roll selection completed successfully.")
        return final_output

    # _build_coherent_candidate_pool, _format_clip_group, _select_and_adjust_sequence
    # 这些内部方法的逻辑是正确的，保持不变
    def _build_coherent_candidate_pool(self, scene_ids: List[int], scenes_map: Dict, gap_threshold: float = 1.0) -> \
            List[Dict]:
        """
        [核心修正] 构建B-roll素材池。
        """
        final_pool = []

        # --- [BUG FIX: 确保场景ID类型一致性] ---
        # 1. 尝试将所有元素转换为整数，以确保正确的数字排序。
        #    如果转换失败，则记录错误并返回空列表，防止程序崩溃。
        try:
            cleaned_scene_ids = [int(sid) for sid in scene_ids]
        except (TypeError, ValueError) as e:
            self.logger.error(f"严重错误: 输入的场景ID列表包含无法转换为整数的元素。原始列表: {scene_ids}", exc_info=True)
            return []
        # --- [BUG FIX END] ---

        # 1. 遍历每一个关联的场景ID
        for sid in sorted(cleaned_scene_ids):
            # 将整数ID转换为字符串，用于查找以字符串为键的 scenes_map
            scene = scenes_map.get(str(sid))
            if not scene: continue

            dialogues_in_scene = scene.get("dialogues", [])
            if not dialogues_in_scene: continue

            # 2. 在单个场景内部进行对话分组
            scene_clips = []
            current_group = []
            for dialogue in dialogues_in_scene:
                if not current_group:
                    current_group.append(dialogue)
                else:
                    prev_end_time = self._time_str_to_seconds(current_group[-1]['end_time'])
                    current_start_time = self._time_str_to_seconds(dialogue['start_time'])
                    if current_start_time - prev_end_time < gap_threshold:
                        current_group.append(dialogue)
                    else:
                        # 上一个连贯组结束，处理并存入当前场景的片段列表
                        scene_clips.append(self._format_clip_group(current_group, sid))
                        current_group = [dialogue]  # 开始一个新组

            # 处理当前场景的最后一组
            if current_group:
                scene_clips.append(self._format_clip_group(current_group, sid))

            # 3. 将当前场景生成的所有片段，加入最终的素材池
            final_pool.extend(scene_clips)

        return final_pool

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

    def _select_and_adjust_sequence(self, narration_text: str, target_duration: float, candidate_pool: List[Dict],
                                    **kwargs) -> List[Dict]:
        rich_candidate_list = self._build_rich_candidate_list(candidate_pool)

        # [确认] 这里使用的是 kwargs.get('lang', 'zh')
        # 由于我们在 execute 中已经更新了 kwargs['lang'] 为 current_lang
        # 这里的逻辑现在是安全的，会正确获取到 'en' (或者 yaml 中的配置)
        prompt = self._build_prompt(
            prompt_name='broll_sequence_selector', lang=kwargs.get('lang', 'zh'),
            narration=narration_text, target_duration=target_duration, rich_candidate_list=rich_candidate_list
        )
        try:
            response_data, _ = self.gemini_processor.generate_content(
                model_name=kwargs.get('default_model', 'gemini-2.5-flash'),
                prompt=prompt,
                temperature=kwargs.get('default_temp', 0.1)
            )

            # [新增] 契约校验
            validated = BrollSelectionResponse(**response_data)
            selected_ids_str = validated.selected_ids

            # 后续逻辑保持不变，因为 selected_ids_str 已经被保证是 List[str]
            selected_indices = [int(sid.replace("ID-", "")) for sid in selected_ids_str]
            selected_clips = [candidate_pool[i] for i in selected_indices if 0 <= i < len(candidate_pool)]

        except RateLimitException as e:
            raise e  # 透传限流异常
        except ValidationError as e:
            self.logger.error(f"B-Roll Schema Failed: {e}")
            # 抛出推理错误
            raise BizException(ErrorCode.LLM_INFERENCE_ERROR, msg=f"Invalid B-Roll JSON: {e}")
        except Exception as e:
            self.logger.warning(f"AI序列选择失败 (Unknown): {e}")
            return []  # 对于其他未知错误，可以保留降级策略，或者也抛出异常

        if not selected_clips: return []
        actual_duration = sum(c['duration'] for c in selected_clips)
        duration_delta = actual_duration - target_duration
        min_clip_duration = 0.5
        if duration_delta > 0.1:
            overshoot_to_trim = duration_delta
            for clip in reversed(selected_clips):
                if overshoot_to_trim <= 0: break
                max_trimmable = clip['duration'] - min_clip_duration
                if max_trimmable <= 0: continue
                trim_amount = min(overshoot_to_trim, max_trimmable)
                new_duration = clip['duration'] - trim_amount
                new_end_time_sec = self._time_str_to_seconds(clip['start_time']) + new_duration
                clip['end_time'] = self._seconds_to_time_str(new_end_time_sec)
                clip['original_duration'] = clip['duration']
                clip['duration'] = round(new_duration, 3)
                overshoot_to_trim -= trim_amount
        elif duration_delta < -0.1:
            last_clip = selected_clips[-1]
            new_duration = last_clip['duration'] - duration_delta
            new_end_time_sec = self._time_str_to_seconds(last_clip['start_time']) + new_duration
            last_clip['end_time'] = self._seconds_to_time_str(new_end_time_sec)
            last_clip['original_duration'] = last_clip['duration']
            last_clip['duration'] = round(new_duration, 3)
        return selected_clips

    def _build_rich_candidate_list(self, candidate_pool: list) -> str:
        """
        为 BrollSelectorService 服务，渲染包含片段类型和时长的富文本素材列表。
        [重构] 使用 localized labels 并在单行内展示完整对话内容。
        """
        # 获取标签，提供英文作为硬编码兜底
        lbl_no_cand = self.labels.get('no_candidates', '(No candidate clips)')
        lbl_type_group = self.labels.get('clip_type_group', 'Coherent Dialogue')
        lbl_type_single = self.labels.get('clip_type_single', 'Single Dialogue')
        lbl_duration = self.labels.get('duration_label', 'Duration')
        lbl_summary = self.labels.get('content_summary_label', 'Content Summary')

        if not candidate_pool:
            return lbl_no_cand

        lines = []
        for i, clip in enumerate(candidate_pool):
            clip_type = lbl_type_group if clip.get('is_group') else lbl_type_single

            # [核心修复]
            # 之前：clip.get('content', 'N/A').split('\n')[0] -> 只取第一行
            # 现在：将换行符替换为 " | " 分隔符，保留所有对话内容，同时保持单行格式方便 AI 阅读
            raw_content = clip.get('content', 'N/A')
            content_summary = raw_content.replace('\n', ' | ')

            lines.append(
                f"ID-{i}: [{clip_type}] {lbl_duration}: {clip.get('duration')}s, {lbl_summary}: {content_summary}"
            )
        return "\n".join(lines)