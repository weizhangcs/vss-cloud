# tests/run_narration_rag_probe.py
# 描述: [Stage 1.5] RAG 检索探针 - 验证 Query 是否能命中有效上下文
# 运行方式: python tests/run_narration_rag_probe.py

import sys
import os
from pathlib import Path
import logging

# --- Google Vertex AI SDK ---
import vertexai
from vertexai.preview import rag  # 注意：根据 SDK 版本，可能需要从 preview 导入

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# 导入引导程序
from utils.local_execution_bootstrap import bootstrap_local_env_and_logger


# ==============================================================================
# 复用 Stage 1 的核心逻辑 (为了方便单文件运行，此处直接包含类定义)
# ==============================================================================
class NarrationQueryBuilder:
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
        control = config.get("control_params", {})
        focus_key = control.get("narrative_focus", "general")
        base_template = self.FOCUS_TEMPLATES.get(focus_key, self.FOCUS_TEMPLATES["general"])
        query_parts = [base_template.format(series_name=series_name)]

        # Scope
        scope = control.get("scope", {})
        if scope.get("type") == "episode_range":
            start, end = scope.get("value", [1, 1])
            query_parts.append(f"请重点关注第 {start} 集到第 {end} 集之间的剧情。")

        # Character Focus
        char_focus = control.get("character_focus", {})
        if char_focus.get("mode") == "specific":
            chars = char_focus.get("characters", [])
            if chars:
                query_parts.append(f"请特别提取与角色“{'、'.join(chars)}”直接相关的戏份和互动。")

        final_query = " ".join(query_parts)
        self.logger.info(f"🔍 [QueryBuilder] 生成查询: {final_query}")
        return final_query


# ==============================================================================
# Stage 1.5: RAG 探测逻辑
# ==============================================================================
class RagProbe:
    def __init__(self, project_id: str, location: str, logger: logging.Logger):
        self.logger = logger
        self.project_id = project_id
        self.location = location

        # 初始化 Vertex AI
        vertexai.init(project=project_id, location=location)
        self.logger.info(f"✅ Vertex AI Initialized (Project: {project_id}, Location: {location})")

    def find_corpus_by_name(self, series_name: str) -> str:
        """尝试根据剧集名称模糊查找已部署的 Corpus"""
        self.logger.info(f"正在查找包含 '{series_name}' 的 RAG 语料库...")
        try:
            corpora = rag.list_corpora()
            for c in corpora:
                # 假设 Corpus Display Name 格式通常包含 series_id
                if series_name in c.display_name:
                    self.logger.info(f"✅ 找到匹配的语料库: {c.display_name} (ID: {c.name})")
                    return c.name

            self.logger.warning(f"❌ 未找到包含 '{series_name}' 的语料库。")
            # 打印所有可用语料库供调试
            available = [c.display_name for c in corpora]
            self.logger.info(f"当前可用语料库: {available}")
            return None
        except Exception as e:
            self.logger.error(f"列出语料库失败: {e}")
            return None

    def probe(self, corpus_name: str, query: str, top_k: int = 10):
        """执行检索并打印结果"""
        if not corpus_name:
            return

        self.logger.info(f"🚀 正在执行 RAG 检索 (Top_k={top_k})...")
        try:
            response = rag.retrieval_query(
                rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
                text=query,
                rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
            )

            contexts = response.contexts.contexts
            self.logger.info(f"✅ 检索成功! 共返回 {len(contexts)} 个片段。")

            print("\n" + "=" * 50)
            print(f"📝 Query: {query}")
            print("=" * 50)

            for i, context in enumerate(contexts):
                # 尝试提取元数据（如果有的话），通常在 context.source_uri 或 text 前几行
                preview = context.text[:200].replace("\n", " ") + "..."
                print(f"\n[Chunk #{i + 1}] (Distance: {context.distance:.4f})")
                print(f"📄 Source: {context.source_uri}")
                print(f"内容预览: {preview}")
                # 如果需要看全文，可以在这里 print(context.text)

            print("\n" + "=" * 50)
            return contexts

        except Exception as e:
            self.logger.error(f"RAG 检索失败: {e}", exc_info=True)


def main():
    # 1. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # [自动补全] 尝试设置 GOOGLE_APPLICATION_CREDENTIALS
    if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
        cred_path = project_root / "conf" / "gcp-credentials.json"
        if cred_path.exists():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)
            logger.info(f"已自动加载凭证: {cred_path}")
        else:
            logger.warning("⚠️ 未找到 conf/gcp-credentials.json，请确保已登录 gcloud 或设置了环境变量")

    # 2. 定义测试场景 (对应之前讨论的 Narrative Config)
    series_name = "20251104-Test"  # 请确保这与您 RAG 里的名字一致（或部分一致）

    test_config = {
        "control_params": {
            "scope": {"type": "episode_range", "value": [1, 8]},  # 试图让 RAG 关注前几集
            "narrative_focus": "romantic_progression",  # 关注情感线
            "character_focus": {
                "mode": "specific",
                "characters": ["车小小", "楚昊轩"]
            }
        }
    }

    # 3. 执行 Stage 1: 构建 Query
    qb = NarrationQueryBuilder(logger)
    query = qb.build(series_name, test_config)

    # 4. 执行 Stage 1.5: RAG 探测
    probe = RagProbe(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        logger=logger
    )

    # 查找语料库
    # 注意：如果模糊匹配失败，您可以临时在这里硬编码 corpus_name = "projects/..."
    corpus_name = probe.find_corpus_by_name(series_name)

    if corpus_name:
        probe.probe(corpus_name, query, top_k=5)


if __name__ == "__main__":
    main()