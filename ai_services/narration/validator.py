# ai_services/narration/validator.py

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple


class NarrationValidator:
    """
    [New Component] 负责解说词的时长校验与可行性分析。
    核心逻辑：
    1. 计算关联场景的物理总时长 (Visual Duration)。
    2. 基于语速预测解说词时长 (Audio Duration)。
    3. 判定是否溢出 (Overflow)。
    """

    def __init__(self, blueprint_data: Dict, config: Dict[str, Any], logger: logging.Logger):
        self.blueprint_data = blueprint_data
        self.scenes_map = self.blueprint_data.get("scenes", {})
        self.logger = logger

        # 从配置中读取语速参数，默认为中文常见语速
        # speaking_rate: 字符/秒 (Chars per Second)
        # 通常：中文约 4.0 - 5.0 字/秒，英文约 2.5 - 3.0 词/秒
        self.speaking_rate = config.get("speaking_rate", 4.2)

        # TTS 引擎的单次合成上限 (例如 CosyVoice 30s)
        self.max_tts_duration = config.get("max_tts_segment_seconds", 30.0)

    def _parse_time_str(self, time_str: str) -> float:
        """
        解析 "HH:MM:SS.mmm" 格式的时间字符串为秒数。
        示例: "00:00:14.330" -> 14.33
        """
        try:
            # 处理 Ass 字幕格式的时间戳
            t = datetime.strptime(time_str.strip(), "%H:%M:%S.%f")
            delta = timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond)
            return delta.total_seconds()
        except Exception as e:
            self.logger.error(f"Failed to parse time string '{time_str}': {e}")
            return 0.0

    def calculate_visual_duration(self, scene_ids: List[int]) -> float:
        """
        计算一组场景的总物理时长。
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
        """
        # 简单过滤掉标点符号可能会更准，但为了安全起见，按全字符计算
        # 可以在此处针对中英文做不同处理
        return round(len(text) / self.speaking_rate, 2)

    def validate_snippet(self, snippet: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        校验单个解说片段。
        返回: (is_valid, validation_info)
        """
        text = snippet.get("narration", "")
        scene_ids = snippet.get("source_scene_ids", [])

        if not text or not scene_ids:
            return True, {}  # 无法校验，默认放行

        visual_duration = self.calculate_visual_duration(scene_ids)
        audio_duration = self.predict_audio_duration(text)

        # 设定一个缓冲阈值 (例如：音频不能超过画面的 95%，留点呼吸感)
        # 或者允许轻微溢出 (例如 105%)，依靠后期剪辑加速？
        # 这里我们采取严格模式：音频 <= 画面
        is_visual_overflow = audio_duration > visual_duration

        # 检查 TTS 限制 (TODO 2.2 预留)
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
                f"Snippet: {text[:20]}..."
            )
            return False, info

        return True, info

    def check_long_sentence_split(self, text: str) -> List[str]:
        """
        [保留方法 2.2] 用于未来实现长句拆分逻辑。
        当前仅作为占位符。
        """
        # TODO: 如果 audio_duration > 30s 但 visual_duration 足够长，
        # 需要在这里利用 NLP 工具对 text 进行语义断句。
        pass