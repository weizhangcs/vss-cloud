# ai_services/biz_services/scene_pre_annotator/i18n.py

from .schemas import ShotType, VisualMood

TRANSLATIONS = {
    "zh": {
        # ... (保持原有的中文映射) ...
        ShotType.EXTREME_CLOSE_UP: "极特写",
        ShotType.CLOSE_UP: "特写",
        ShotType.MEDIUM_SHOT: "中景",
        ShotType.LONG_SHOT: "全景",
        ShotType.ESTABLISHING_SHOT: "建立镜头(环境)",
        VisualMood.NEUTRAL: "中性",
        VisualMood.WARM: "温暖",
        VisualMood.COLD: "冷峻",
        VisualMood.DARK_TENSE: "阴暗/紧张",
        VisualMood.BRIGHT_CHEERFUL: "明亮/愉快",
        # NarrativeFunction (如果还有用到)
    },
    "en": {
        # English Formatting
        ShotType.EXTREME_CLOSE_UP: "Extreme Close Up",
        ShotType.CLOSE_UP: "Close Up",
        ShotType.MEDIUM_SHOT: "Medium Shot",
        ShotType.LONG_SHOT: "Long Shot",
        ShotType.ESTABLISHING_SHOT: "Establishing Shot",
        VisualMood.NEUTRAL: "Neutral",
        VisualMood.WARM: "Warm",
        VisualMood.COLD: "Cold",
        VisualMood.DARK_TENSE: "Dark/Tense",
        VisualMood.BRIGHT_CHEERFUL: "Bright/Cheerful",
    }
}

def get_localized_term(enum_val, lang: str) -> str:
    # 如果没传 lang，默认回退到 'en' 只是为了安全，
    # 但业务逻辑应该保证 lang 存在
    target = TRANSLATIONS.get(lang, TRANSLATIONS.get("en", {}))
    return target.get(enum_val, enum_val.value)