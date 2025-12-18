# ai_services/narration/query_builder.py

import json
import logging
from pathlib import Path
from typing import Dict, Any

# [New] 引入强类型配置定义
from ai_services.biz_services.narration.schemas import NarrationServiceConfig


class NarrationQueryBuilder:
    def __init__(self, metadata_dir: Path, logger: logging.Logger):
        self.logger = logger
        self.templates_data = self._load_templates(metadata_dir)

    def _load_templates(self, metadata_dir: Path) -> Dict:
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

    # [Refactor] 接收 NarrationServiceConfig 对象
    def build(self, config: NarrationServiceConfig) -> str:
        """
        构建最终的 RAG 检索查询字符串。
        """
        # [Type Safe Access] 直接属性访问
        lang = config.lang
        asset_name = config.asset_name or "Unknown Asset"

        lang_pack = self.templates_data.get(lang) or self.templates_data.get("en") or {}
        control = config.control_params

        # --- 1. Narrative Focus (支持 Custom) ---
        focus_key = control.narrative_focus

        if focus_key == "custom":
            # [Type Safe] custom_prompts 是 Optional[CustomPrompts] 对象
            custom_prompts = control.custom_prompts
            if custom_prompts and custom_prompts.narrative_focus:
                base_template = custom_prompts.narrative_focus
                self.logger.info(f"Using CUSTOM Narrative Focus: {base_template[:50]}...")
            else:
                # Fallback (理论上 Validator 会拦截，这里做二次防御)
                base_template = f"{asset_name}"
        else:
            focus_templates = lang_pack.get("focus", {})
            base_template = focus_templates.get(focus_key)
            if not base_template:
                base_template = focus_templates.get("general", f"{asset_name}")

        query_parts = [self._safe_format(base_template, asset_name=asset_name)]

        # --- 2. Scope ---
        scope = control.scope
        scope_templates = lang_pack.get("scope", {})

        if scope.type == "episode_range":
            # scope.value 是 Optional[List[int]]
            vals = scope.value or [1, 1]
            if len(vals) >= 2:
                start, end = vals[0], vals[1]
                tpl = scope_templates.get("episode_range", "")
                if tpl:
                    query_parts.append(self._safe_format(tpl, start=start, end=end))
        elif scope.type == "scene_selection":
            tpl = scope_templates.get("scene_selection", "")
            if tpl:
                query_parts.append(tpl)

        # --- 3. Character Focus ---
        char_focus = control.character_focus
        char_templates = lang_pack.get("character", {})

        if char_focus.mode == "specific":
            chars = char_focus.characters
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