# ai_services/narration/query_builder.py

import json
import logging
from pathlib import Path
from typing import Dict, Any


class NarrationQueryBuilder:
    def __init__(self, metadata_dir: Path, logger: logging.Logger):
        self.logger = logger
        self.templates_data = self._load_templates(metadata_dir)

    def _load_templates(self, metadata_dir: Path) -> Dict:
        # ... (保持不变) ...
        template_path = metadata_dir / "query_templates.json"
        if not template_path.is_file():
            self.logger.warning(f"Query templates not found at {template_path}, using empty defaults.")
            return {}
        try:
            with template_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load query templates: {e}")
            return {}

    def _safe_format(self, template: str, **kwargs) -> str:
        # ... (保持不变) ...
        try:
            return template.format(**kwargs)
        except KeyError as e:
            missing_key = str(e).strip("'")
            # 简单的降级策略
            kwargs[missing_key] = f"[{missing_key}]"
            try:
                return template.format(**kwargs)
            except Exception:
                return template
        except Exception as e:
            self.logger.error(f"Format error: {e}")
            return template

    def build(self, asset_name: str, config: Dict[str, Any]) -> str:
        """
        构建最终的 RAG 检索查询字符串。
        """
        lang = config.get("lang", "zh")
        lang_pack = self.templates_data.get(lang) or self.templates_data.get("en") or {}

        control = config.get("control_params", {})

        # --- 1. Narrative Focus (支持 Custom) ---
        focus_key = control.get("narrative_focus", "general")

        # [核心修改] 判断是否为 Custom
        if focus_key == "custom":
            # 从 custom_prompts 中获取
            custom_prompts = control.get("custom_prompts") or {}
            # 注意：config 是 dict，因为 QueryBuilder 还没完全迁移到 Pydantic 对象
            # 这里的 config.get("control_params") 是一个 dict
            # 但在上游 Validation 后，它可能是 model.dict()，所以这种写法是兼容的

            # 如果是 dict 形式的 config
            base_template = custom_prompts.get("narrative_focus", f"{asset_name}")
            self.logger.info(f"Using CUSTOM Narrative Focus: {base_template[:50]}...")
        else:
            # 使用预设模版
            focus_templates = lang_pack.get("focus", {})
            base_template = focus_templates.get(focus_key)
            if not base_template:
                base_template = focus_templates.get("general", f"{asset_name}")

        query_parts = [self._safe_format(base_template, asset_name=asset_name)]

        # --- 2. Scope ---
        scope = control.get("scope", {})
        scope_templates = lang_pack.get("scope", {})

        if scope.get("type") == "episode_range":
            start, end = scope.get("value", [1, 1])
            tpl = scope_templates.get("episode_range", "")
            if tpl:
                query_parts.append(self._safe_format(tpl, start=start, end=end))
        elif scope.get("type") == "scene_selection":
            tpl = scope_templates.get("scene_selection", "")
            if tpl:
                query_parts.append(tpl)

        # --- 3. Character Focus ---
        char_focus = control.get("character_focus", {})
        char_templates = lang_pack.get("character", {})

        if char_focus.get("mode") == "specific":
            chars = char_focus.get("characters", [])
            if chars:
                char_str = "、".join(chars) if lang == "zh" else ", ".join(chars)
                tpl = char_templates.get("specific", "")
                if tpl:
                    query_parts.append(self._safe_format(tpl, chars=char_str))

        # 5. 组装最终 Query
        final_query = " ".join(query_parts)

        if not final_query.strip():
            fallback = f"{asset_name} story summary"
            return fallback

        self.logger.info(f"🔍 [QueryBuilder] Generated Query: {final_query}")
        return final_query