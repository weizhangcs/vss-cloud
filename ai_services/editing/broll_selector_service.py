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

            clip_sequence = self._select_and_adjust_sequence(entry["narration"], target_duration, candidate_pool,
                                                             **kwargs)

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
        新逻辑：对每个场景独立进行对话分组，然后将所有结果合并。
        """
        final_pool = []

        # 1. 遍历每一个关联的场景ID
        for sid in sorted(scene_ids):
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
        prompt = self._build_prompt(
            prompt_name='broll_sequence_selector', lang=kwargs.get('lang', 'zh'),
            narration=narration_text, target_duration=target_duration, rich_candidate_list=rich_candidate_list
        )
        try:
            response_data, _ = self.gemini_processor.generate_content(
                model_name=kwargs.get('model', 'gemini-1.5-pro-latest'), prompt=prompt, temperature=0.1
            )
            selected_ids_str = response_data.get("selected_ids", [])
            selected_indices = [int(sid.replace("ID-", "")) for sid in selected_ids_str]
            selected_clips = [candidate_pool[i] for i in selected_indices if 0 <= i < len(candidate_pool)]
        except Exception as e:
            self.logger.warning(f"AI序列选择失败，将回退到简单算法: {e}")
            return []
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

        Args:
            candidate_pool (list): 由 _build_coherent_candidate_pool 生成的素材池。

        Returns:
            str: 格式化后供AI prompt使用的纯文本。
        """
        if not candidate_pool:
            return "(无候选素材)"
        lines = []
        for i, clip in enumerate(candidate_pool):
            # 注意：这里的标签是硬编码的，如果未来需要国际化，可以从self.labels中获取
            clip_type = "连贯对话" if clip.get('is_group') else "独立对话"
            content_summary = clip.get('content', 'N/A').split('\n')[0]  # 只取第一行作为摘要
            lines.append(f"ID-{i}: [{clip_type}] 时长: {clip.get('duration')}s, 内容摘要: {content_summary}")
        return "\n".join(lines)