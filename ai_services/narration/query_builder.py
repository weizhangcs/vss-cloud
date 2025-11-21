# ai_services/narration/query_builder.py

import json
import logging
from pathlib import Path
from typing import Dict, Any


class NarrationQueryBuilder:
    """
    [Stage 1] 意图理解与查询构建器。

    职责：
        负责将结构化的配置参数 (narration_config) 翻译为 RAG 检索引擎可理解的自然语言 Query。
        支持基于 i18n 的模版加载。

    依赖：
        - metadata/query_templates.json: 存储多语言的查询模版片段。
    """

    def __init__(self, metadata_dir: Path, logger: logging.Logger):
        """
        初始化查询构建器。

        Args:
            metadata_dir: 包含 query_templates.json 的目录路径。
            logger: 日志记录器。
        """
        self.logger = logger
        self.templates_data = self._load_templates(metadata_dir)

    def _load_templates(self, metadata_dir: Path) -> Dict:
        """加载 JSON 格式的查询模版文件。"""
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

    def build(self, series_name: str, config: Dict[str, Any]) -> str:
        """
        构建最终的 RAG 检索查询字符串。

        Args:
            series_name: 剧集名称。
            config: 包含 'control_params' 和 'lang' 的配置字典。

        Returns:
            str: 拼接好的自然语言查询语句。
        """
        # 1. 确定语言环境 (默认回退策略: 指定语言 -> 英文 -> 中文)
        lang = config.get("lang", "zh")
        lang_pack = self.templates_data.get(lang, self.templates_data.get("en", {}))

        control = config.get("control_params", {})

        # 2. 确定核心叙事焦点 (Narrative Focus)
        # 这是 Query 的主干，决定了检索的主题方向
        focus_templates = lang_pack.get("focus", {})
        focus_key = control.get("narrative_focus", "general")
        # 如果指定的 focus_key 不存在，回退到 general 模版；若 general 也不存在，使用剧集名兜底
        base_template = focus_templates.get(focus_key, focus_templates.get("general", f"{series_name}"))

        query_parts = [base_template.format(series_name=series_name)]

        # 3. 处理剧情范围约束 (Scope)
        # 虽然 RAG 是语义检索，但在 Query 中明确范围有助于模型理解上下文
        scope = control.get("scope", {})
        scope_templates = lang_pack.get("scope", {})

        if scope.get("type") == "episode_range":
            start, end = scope.get("value", [1, 1])
            tpl = scope_templates.get("episode_range", "")
            if tpl:
                query_parts.append(tpl.format(start=start, end=end))
        elif scope.get("type") == "scene_selection":
            tpl = scope_templates.get("scene_selection", "")
            if tpl:
                query_parts.append(tpl)

        # 4. 处理角色聚焦 (Character Focus)
        # 显式要求模型关注特定角色的戏份
        char_focus = control.get("character_focus", {})
        char_templates = lang_pack.get("character", {})

        if char_focus.get("mode") == "specific":
            chars = char_focus.get("characters", [])
            if chars:
                # 根据语言习惯处理列表连接符
                char_str = "、".join(chars) if lang == "zh" else ", ".join(chars)
                tpl = char_templates.get("specific", "")
                if tpl:
                    query_parts.append(tpl.format(chars=char_str))

        # 5. 组装最终 Query
        final_query = " ".join(query_parts)
        self.logger.info(f"🔍 [QueryBuilder] Generated Query: {final_query}")

        return final_query