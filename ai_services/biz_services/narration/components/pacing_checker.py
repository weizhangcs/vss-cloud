import logging
from typing import Dict, Any, Tuple, List

# [核心依赖] 强类型 Dataset
from ai_services.biz_services.narrative_dataset import NarrativeDataset

logger = logging.getLogger(__name__)


class NarrationPacingChecker:
    """
    [Biz Component] 解说词节奏检查器。
    职责：基于 Dataset 对象，计算解说词片段关联场景的视觉时长，判断是否溢出。
    优势：直接使用 Pydantic Computed Fields，无需重复解析时间字符串。
    """

    def __init__(self, dataset: NarrativeDataset, config: Dict, logger: logging.Logger):
        self.dataset = dataset
        self.logger = logger

        # 兼容处理：config 可能是 Pydantic 对象或 Dict
        if hasattr(config, "model_dump"):
            params = config.model_dump()
        else:
            params = config

        service_params = params.get("service_params", params)

        # 语言参数决定语速
        lang = params.get("lang", "zh")
        default_rate = 3.0 if lang == 'zh' else 2.5

        self.speaking_rate = float(service_params.get("speaking_rate", default_rate))
        self.tolerance_ratio = float(service_params.get("tolerance_ratio", 0.2))

    def check_pacing(self, snippet: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        检查单条解说词的步调。
        """
        text = snippet.get("narration", "")
        scene_ids = snippet.get("source_scene_ids", [])

        total_visual_duration = 0.0
        found_ids = []

        # [Object Access] 直接访问 Dataset 对象
        for sid in scene_ids:
            # Dataset V6 的 keys 是 str 类型
            sid_str = str(sid)
            scene = self.dataset.scenes.get(sid_str)

            if scene:
                # [Optimization] 直接使用 Computed Field 'duration'
                # 如果 duration 为 0 (例如只有 start_time 无 end_time 的异常数据)，兜底为 0
                dur = getattr(scene, 'duration', 0.0)
                total_visual_duration += dur
                found_ids.append(sid)
            else:
                # 记录找不到的 ID，便于调试 (可能是 RAG 幻觉生成的 ID)
                pass

        # 数据异常报警
        if total_visual_duration <= 0.1 and scene_ids:
            self.logger.warning(
                f"⚠️ Zero Visual Duration detected: Snippet Scenes={scene_ids}, Found={found_ids}. "
                "Skipping pacing check for this snippet."
            )

        # 估算语音时长
        # 简单估算，更精确的估算应由 TTS 引擎预处理提供，这里做业务级守门
        char_count = len(text)  # 这里简化处理，英文应按单词算，但在业务守门层按字数+系数通常足够
        pred_audio_duration = char_count / self.speaking_rate

        # 计算限制
        duration_limit = total_visual_duration * (1 + self.tolerance_ratio)
        is_pacing_ok = pred_audio_duration <= duration_limit

        if total_visual_duration <= 0.1:
            # 此时无法判断溢出，默认为 False 以避免错误 Refine，或者标记为 Skip
            # 在业务上，如果时长为0，Refiner 也救不了，不如直接放行并在 metadata 标记
            is_pacing_ok = True
            overflow_sec = 0.0
        else:
            overflow_sec = max(0.0, pred_audio_duration - duration_limit)

        return is_pacing_ok, {
            "text_len": char_count,
            "pred_audio_duration": round(pred_audio_duration, 2),
            "real_visual_duration": round(total_visual_duration, 2),
            "duration_limit": round(duration_limit, 2),
            "overflow_sec": round(overflow_sec, 2),
            "is_overflow": not is_pacing_ok,
            "tolerance_ratio": self.tolerance_ratio
        }