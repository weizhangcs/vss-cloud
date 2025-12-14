# tests/run_narration_generator_v2.py
# 描述: [Stage 1] Narration Generator V2 开发工作台 - 聚焦于 "Query 构建"
# 运行方式: python tests/run_narration_generator_v2.py

import sys
from pathlib import Path
import json
import logging

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

# 导入引导程序 (用于加载 .env 等)
from tests.lib.bootstrap import bootstrap_local_env_and_logger


class NarrationQueryBuilder:
    """
    [核心逻辑] 负责将结构化的 narration_config 翻译为 RAG 检索用的自然语言 Query。
    """

    # 预定义的“叙事焦点”模版库
    FOCUS_TEMPLATES = {
        "general": "剧集“{series_name}”的完整剧情发展，包括主要冲突、高潮和结局。",
        "romantic_progression": "剧集“{series_name}”中男女主角的情感发展脉络，包括初识、误会、冲突、升温和最终结局。",
        "business_success": "剧集“{series_name}”中主角如何克服职场困难，完成商业复仇或取得成功的关键事件。",
        "suspense_reveal": "剧集“{series_name}”中埋藏最大的悬念、秘密线索，以及最终的反转真相。",
        "character_growth": "剧集“{series_name}”中主角个人的成长弧光，性格转变的关键节点。"
    }

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def build(self, series_name: str, config: dict) -> str:
        """
        构建最终查询字符串。
        """
        control = config.get("control_params", {})

        # 1. 确定基础模版 (基于 narrative_focus)
        focus_key = control.get("narrative_focus", "general")
        base_template = self.FOCUS_TEMPLATES.get(focus_key, self.FOCUS_TEMPLATES["general"])
        query_parts = [base_template.format(series_name=series_name)]

        # 2. 处理范围约束 (Scope)
        # 注意：RAG 还是会检索全文，但我们在 Query 中强调范围，有助于让语义模型更关注相关章节的描述
        scope = control.get("scope", {})
        scope_type = scope.get("type")
        if scope_type == "episode_range":
            start, end = scope.get("value", [1, 1])
            query_parts.append(f"请重点关注第 {start} 集到第 {end} 集之间的剧情。")
        elif scope_type == "scene_selection":
            query_parts.append("请重点关注指定场景列表中的剧情细节。")

        # 3. 处理角色聚焦 (Character Focus)
        char_focus = control.get("character_focus", {})
        if char_focus.get("mode") == "specific":
            chars = char_focus.get("characters", [])
            if chars:
                char_str = "、".join(chars)
                query_parts.append(f"请特别提取与角色“{char_str}”直接相关的戏份和互动。")

        # 4. 组装最终 Query
        final_query = " ".join(query_parts)

        self.logger.info(f"构建 Query: [{focus_key}] -> {final_query}")
        return final_query


def run_test_cases(builder: NarrationQueryBuilder):
    """
    模拟不同的业务场景，验证 Query 构建逻辑是否符合预期。
    """
    print("\n" + "=" * 20 + " 开始测试用例 (Stage 1: Query Builder) " + "=" * 20)

    # --- 用例 A: 默认全剧解说 ---
    config_a = {
        "control_params": {
            "scope": {"type": "full"},
            "narrative_focus": "general"
        }
    }
    print("\n🔹 [Case A] 默认全剧解说:")
    print(f"   输出: {builder.build('总裁的契约女友', config_a)}")

    # --- 用例 B: 只看前5集的感情线 (针对车小小和楚昊轩) ---
    config_b = {
        "control_params": {
            "scope": {"type": "episode_range", "value": [1, 5]},
            "narrative_focus": "romantic_progression",
            "character_focus": {
                "mode": "specific",
                "characters": ["车小小", "楚昊轩"]
            }
        }
    }
    print("\n🔹 [Case B] 前5集男女主感情线:")
    print(f"   输出: {builder.build('总裁的契约女友', config_b)}")

    # --- 用例 C: 悬疑反转 (无角色限制) ---
    config_c = {
        "control_params": {
            "scope": {"type": "full"},
            "narrative_focus": "suspense_reveal"
        }
    }
    print("\n🔹 [Case C] 悬疑反转线:")
    print(f"   输出: {builder.build('开端', config_c)}")


def main():
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 1. 实例化构建器
    query_builder = NarrationQueryBuilder(logger)

    # 2. 运行逻辑验证
    run_test_cases(query_builder)

    # TODO (Stage 1.5): 这里将在下一步接入真实的 Vertex AI RAG
    # rag_service = RagService(...)
    # retrieved_docs = rag_service.retrieve(query)


if __name__ == "__main__":
    main()