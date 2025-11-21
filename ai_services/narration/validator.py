# ai_services/narration/validator.py

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple


class NarrationValidator:
    """
    [Stage 4] 解说词校验器。

    职责：
        对生成的解说词进行物理约束检查。
        核心指标是“声画对位”：解说词的朗读时长(Audio Duration)不得超过画面的物理时长(Visual Duration)。
    """

    def __init__(self, blueprint_data: Dict, config: Dict[str, Any], logger: logging.Logger):
        self.blueprint_data = blueprint_data
        self.scenes_map = self.blueprint_data.get("scenes", {})
        self.logger = logger

        # 语速参数 (Chars per Second)
        # 默认中文语速 4.2 字/秒，可从配置中覆盖
        self.speaking_rate = config.get("speaking_rate", 4.2)

        # TTS 引擎的物理限制 (例如单次合成不超过 30s)
        self.max_tts_duration = config.get("max_tts_segment_seconds", 30.0)

    def _parse_time_str(self, time_str: str) -> float:
        """解析 'HH:MM:SS.mmm' 格式的时间字符串为秒数。"""
        try:
            t = datetime.strptime(time_str.strip(), "%H:%M:%S.%f")
            delta = timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond)
            return delta.total_seconds()
        except Exception as e:
            self.logger.error(f"Failed to parse time string '{time_str}': {e}")
            return 0.0

    def calculate_visual_duration(self, scene_ids: List[int]) -> float:
        """
        计算给定场景列表的总物理时长。
        """
        total_duration = 0.0
        for sid in scene_ids:
            scene = self.scenes_map.get(str(sid))
            if not scene:
                continue

            start = self._parse_time_str(scene.get("start_time", "00:00:00.000"))
            end = self._parse_time_str(scene.get("end_time", "00:00:00.000"))

            duration = end - start
            if duration > 0:
                total_duration += duration

        return round(total_duration, 2)

    def predict_audio_duration(self, text: str) -> float:
        """
        基于字数和语速预测音频时长。
        Algorithm: char_count / speaking_rate
        """
        if not text:
            return 0.0
        return round(len(text) / self.speaking_rate, 2)

    def validate_snippet(self, snippet: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        校验单个解说片段。

        Returns:
            (is_valid, validation_info)
            - is_valid: bool, 是否通过校验
            - validation_info: dict, 包含详细的时长数据，供后续流程参考或修正
        """
        text = snippet.get("narration", "")
        scene_ids = snippet.get("source_scene_ids", [])

        if not text or not scene_ids:
            return True, {}

        visual_duration = self.calculate_visual_duration(scene_ids)
        audio_duration = self.predict_audio_duration(text)

        # Rule 1: Audio Overflow (硬性约束)
        # 解说词念完所需时间 > 画面总时间 -> 校验失败
        is_visual_overflow = audio_duration > visual_duration

        # Rule 2: TTS Limitation (软性约束/标记)
        # 单句时长超过 TTS 引擎限制 -> 仅标记，由下游 Dubbing Engine 处理断句
        is_tts_overflow = audio_duration > self.max_tts_duration

        info = {
            "text_len": len(text),
            "pred_audio_duration": audio_duration,
            "real_visual_duration": visual_duration,
            "overflow_sec": round(audio_duration - visual_duration, 2),
            "is_tts_overflow": is_tts_overflow
        }

        if is_visual_overflow:
            self.logger.warning(
                f"Validation Failed: Audio ({audio_duration}s) > Visual ({visual_duration}s). "
                f"Snippet Preview: {text[:20]}..."
            )
            return False, info

        return True, info