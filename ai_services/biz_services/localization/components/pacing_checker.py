import logging
from typing import Dict, Any, Tuple, Optional

from ai_services.biz_services.narrative_dataset import NarrativeDataset

logger = logging.getLogger(__name__)


class LocalizationPacingChecker:
    """
    [Localization Component] 多语种节奏检查器。

    Design Philosophy:
    - Independent: 独立于 Narration 模块，维护自己的语种映射表。
    - Strategy Pattern: 根据目标语言自动选择 'Word-based' 或 'Char-based' 计数策略。
    """

    # 默认语速参考表 (可根据业务持续扩充)
    # word: 词/秒
    # char: 字/秒
    DEFAULT_RATES = {
        # --- Word Based (拉丁/日耳曼语系等) ---
        "en": {"type": "word", "rate": 2.5},  # 英语
        "fr": {"type": "word", "rate": 2.5},  # 法语 (语速较快，但单词较长)
        "es": {"type": "word", "rate": 3.0},  # 西班牙语 (语速极快)
        "de": {"type": "word", "rate": 2.2},  # 德语 (单词很长)
        "it": {"type": "word", "rate": 2.8},  # 意大利语
        "pt": {"type": "word", "rate": 2.5},  # 葡萄牙语
        "ru": {"type": "word", "rate": 2.0},  # 俄语

        # --- Char Based (CJK) ---
        "zh": {"type": "char", "rate": 3.8},  # 中文 (含标点)
        "ja": {"type": "char", "rate": 4.0},  # 日语 (假名密度高)
        "ko": {"type": "char", "rate": 3.5},  # 韩语
    }

    # 兜底策略
    FALLBACK_STRATEGY = {"type": "word", "rate": 2.5}

    def __init__(self,
                 dataset: NarrativeDataset,
                 target_lang: str,
                 user_speaking_rate: Optional[float] = None,
                 tolerance_ratio: float = 0.1,
                 logger: logging.Logger = None):

        self.dataset = dataset
        self.target_lang = target_lang.lower()
        self.logger = logger or logging.getLogger(__name__)

        # 确定策略
        strategy = self.DEFAULT_RATES.get(self.target_lang, self.FALLBACK_STRATEGY)
        self.count_type = strategy["type"]

        # 确定语速 (用户指定的优先级 > 默认表)
        if user_speaking_rate and user_speaking_rate > 0:
            self.speaking_rate = float(user_speaking_rate)
            self.logger.info(
                f"Using user-defined rate for {self.target_lang}: {self.speaking_rate} {self.count_type}s/sec")
        else:
            self.speaking_rate = strategy["rate"]
            self.logger.info(f"Using default rate for {self.target_lang}: {self.speaking_rate} {self.count_type}s/sec")

        self.tolerance_ratio = float(tolerance_ratio)

    def check_pacing(self, snippet: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        检查翻译后的片段是否符合视觉时长。
        """
        text = snippet.get("narration", "")
        scene_ids = snippet.get("source_scene_ids", [])

        # 1. 计算视觉时长 (Visual Duration)
        total_visual_duration = 0.0
        found_ids = []

        for sid in scene_ids:
            sid_str = str(sid)
            scene = self.dataset.scenes.get(sid_str)
            if scene:
                dur = getattr(scene, 'duration', 0.0)
                total_visual_duration += dur
                found_ids.append(sid)

        if total_visual_duration <= 0.1:
            # 无法获取时长，放行，但标记
            return True, {
                "text_len": len(text),
                "count_type": self.count_type,
                "pred_audio_duration": 0.0,
                "real_visual_duration": 0.0,
                "overflow_sec": 0.0,
                "is_overflow": False
            }

        # 2. 计算音频预估时长 (Audio Duration)
        if self.count_type == "word":
            # 简单分词 (对于大多数西方语言，空格分词足够估算)
            # 过滤掉空字符串
            count = len([w for w in text.split() if w.strip()])
        else:
            # 字符计数 (CJK)
            # 移除所有空白字符，避免格式化造成的误差
            count = len("".join(text.split()))

        pred_audio_duration = count / self.speaking_rate

        # 3. 判定
        duration_limit = total_visual_duration * (1 + self.tolerance_ratio)
        is_ok = pred_audio_duration <= duration_limit
        overflow_sec = max(0.0, pred_audio_duration - duration_limit)

        return is_ok, {
            "text_len": count,  # 这里的长度可能是字数，也可能是词数
            "count_unit": self.count_type,  # 明确告知是 word 还是 char
            "pred_audio_duration": round(pred_audio_duration, 2),
            "real_visual_duration": round(total_visual_duration, 2),
            "duration_limit": round(duration_limit, 2),
            "overflow_sec": round(overflow_sec, 2),
            "is_overflow": not is_ok,
            "tolerance_ratio": self.tolerance_ratio
        }