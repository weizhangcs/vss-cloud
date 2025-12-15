# ai_services/narration/validator.py

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple


class NarrationValidator:
    """
    [Stage 4] 解说词校验器 (Strategy Upgrade)。
    """

    def __init__(self, blueprint_data: Dict, config: Dict[str, Any], logger: logging.Logger, lang: str = "zh"):
        self.blueprint_data = blueprint_data
        self.scenes_map = self.blueprint_data.get("scenes", {})
        self.logger = logger

        self.speaking_rate = config.get("speaking_rate", 4.2)
        self.max_tts_duration = config.get("max_tts_segment_seconds", 30.0)

        # [修改] 获取比例系数，默认为 0.0 (严格模式)
        self.overflow_tolerance = config.get("overflow_tolerance", 0.0)
        self.lang = lang  # 这里的 lang 应该是 target_lang

    # ... (_parse_time_str, calculate_visual_duration, predict_audio_duration 保持不变) ...
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
        total_duration = 0.0
        for sid in scene_ids:
            scene = self.scenes_map.get(str(sid))
            if not scene: continue
            start = self._parse_time_str(scene.get("start_time", "00:00:00.000"))
            end = self._parse_time_str(scene.get("end_time", "00:00:00.000"))
            duration = end - start
            if duration > 0: total_duration += duration
        return round(total_duration, 2)

    def predict_audio_duration(self, text: str) -> float:
        if self.lang == "zh":
            # 中文按字符算
            return round(len(text) / self.speaking_rate, 2)
        else:
            # 英文按单词算 : TODO： 可能要维护一个不同语言的词表
            return round(len(text.split()) / self.speaking_rate, 2)

    def validate_snippet(self, snippet: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        校验单个解说片段。
        """
        text = snippet.get("narration", "")
        scene_ids = snippet.get("source_scene_ids", [])

        if not text or not scene_ids:
            return True, {}

        visual_duration = self.calculate_visual_duration(scene_ids)
        audio_duration = self.predict_audio_duration(text)

        # [核心修改] 策略计算
        # limit = visual * (1 + tolerance)
        # e.g., tolerance -0.15 => limit = visual * 0.85
        duration_limit = visual_duration * (1.0 + self.overflow_tolerance)

        # 判断溢出：Audio > Limit
        is_visual_overflow = audio_duration > duration_limit

        is_tts_overflow = audio_duration > self.max_tts_duration

        info = {
            "text_len": len(text),
            "pred_audio_duration": audio_duration,
            "real_visual_duration": visual_duration,
            "duration_limit": round(duration_limit, 2),  # 记录一下计算出的红线
            "overflow_sec": round(audio_duration - duration_limit, 2),  # 超过红线多少
            "is_tts_overflow": is_tts_overflow,
            "tolerance_ratio": self.overflow_tolerance
        }

        if is_visual_overflow:
            self.logger.warning(
                f"Validation Failed: Audio ({audio_duration}s) > Limit ({duration_limit}s) "
                f"[Visual {visual_duration}s * (1+{self.overflow_tolerance})]. "
                f"Snippet Preview: {text[:20]}..."
            )
            return False, info

        return True, info