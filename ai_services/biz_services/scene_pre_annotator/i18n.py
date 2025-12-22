# ai_services/biz_services/scene_pre_annotator/i18n.py

# [Modify] 移除 VisualMood 的引入
from .schemas import ShotType

TRANSLATIONS = {
    "zh": {
        # ShotType 保留
        ShotType.EXTREME_CLOSE_UP: "极特写",
        ShotType.CLOSE_UP: "特写",
        ShotType.MEDIUM_SHOT: "中景",
        ShotType.LONG_SHOT: "全景",
        ShotType.ESTABLISHING_SHOT: "建立镜头(环境)",

        # [Deleted] VisualMood 相关映射已移除，因为现在由 TagManager + DB 动态管理
    },
    "en": {
        # ShotType 保留
        ShotType.EXTREME_CLOSE_UP: "Extreme Close Up",
        ShotType.CLOSE_UP: "Close Up",
        ShotType.MEDIUM_SHOT: "Medium Shot",
        ShotType.LONG_SHOT: "Long Shot",
        ShotType.ESTABLISHING_SHOT: "Establishing Shot",

        # [Deleted] VisualMood 相关映射已移除
    }
}


def get_localized_term(enum_val, lang: str) -> str:
    """
    仅用于 ShotType 等静态枚举的翻译。
    动态标签 (visual_mood_tags) 请使用 TagManager.get_display_label (如果需要) 或直接使用清洗后的值。
    """
    # 如果没传 lang，默认回退到 'en'
    target = TRANSLATIONS.get(lang, TRANSLATIONS.get("en", {}))
    # 如果找不到 key，返回 value 本身 (str)
    return target.get(enum_val, getattr(enum_val, 'value', str(enum_val)))