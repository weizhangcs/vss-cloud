# ai_services/narration/query_builder.py
import json
import logging
from pathlib import Path
from typing import Dict, Any


class NarrationQueryBuilder:
    """
    [Stage 1] 负责将结构化的 narration_config 翻译为 RAG 检索用的自然语言 Query。
    """

    def __init__(self, metadata_dir: Path, logger: logging.Logger):
        self.logger = logger
        self.templates_data = self._load_templates(metadata_dir)

    def _load_templates(self, metadata_dir: Path) -> Dict:
        template_path = metadata_dir / "query_templates.json"
        if not template_path.is_file():
            self.logger.warning(f"Query templates not found at {template_path}")
            return {}
        try:
            with template_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load query templates: {e}")
            return {}

    def build(self, series_name: str, config: Dict[str, Any]) -> str:
        lang = config.get("lang", "zh")
        lang_pack = self.templates_data.get(lang, self.templates_data.get("en", {}))
        control = config.get("control_params", {})

        # 1. Focus
        focus_templates = lang_pack.get("focus", {})
        focus_key = control.get("narrative_focus", "general")
        base_template = focus_templates.get(focus_key, focus_templates.get("general", f"{series_name}"))
        query_parts = [base_template.format(series_name=series_name)]

        # 2. Scope
        scope = control.get("scope", {})
        scope_templates = lang_pack.get("scope", {})
        if scope.get("type") == "episode_range":
            start, end = scope.get("value", [1, 1])
            tpl = scope_templates.get("episode_range", "")
            if tpl: query_parts.append(tpl.format(start=start, end=end))
        elif scope.get("type") == "scene_selection":
            tpl = scope_templates.get("scene_selection", "")
            if tpl: query_parts.append(tpl)

        # 3. Character
        char_focus = control.get("character_focus", {})
        char_templates = lang_pack.get("character", {})
        if char_focus.get("mode") == "specific":
            chars = char_focus.get("characters", [])
            if chars:
                char_str = "、".join(chars) if lang == "zh" else ", ".join(chars)
                tpl = char_templates.get("specific", "")
                if tpl: query_parts.append(tpl.format(chars=char_str))

        final_query = " ".join(query_parts)
        self.logger.info(f"🔍 [QueryBuilder] Generated Query: {final_query}")
        return final_query