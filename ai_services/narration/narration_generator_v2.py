# ai_services/narration/narration_generator_v2.py
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import vertexai
from vertexai import rag

# 导入基础 Mixin 和 Processor
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor
# 导入 RAG 辅助函数 (用于加载全局 i18n)
from ai_services.rag.schemas import load_i18n_strings

# 导入 V2 组件
from .query_builder import NarrationQueryBuilder
from .context_enhancer import ContextEnhancer


class NarrationGeneratorV2(AIServiceMixin):
    """
    [V2] 智能解说词生成服务。
    采用 "Query -> Enhance -> Synthesize" 三段式架构。
    """
    SERVICE_NAME = "narration_generator"

    def __init__(self,
                 project_id: str,
                 location: str,
                 prompts_dir: Path,
                 metadata_dir: Path,  # [新增] 存放 templates.json 和 styles.json
                 rag_schema_path: Path,  # [新增] RAG 语言包路径
                 logger: logging.Logger,
                 work_dir: Path,
                 gemini_processor: GeminiProcessor):

        self.project_id = project_id
        self.location = location
        self.prompts_dir = prompts_dir
        self.metadata_dir = metadata_dir
        self.logger = logger
        self.work_dir = work_dir
        self.gemini_processor = gemini_processor

        # 初始化 Vertex AI
        vertexai.init(project=self.project_id, location=self.location)

        # 初始化全局 RAG 语言包 (ContextEnhancer 依赖它)
        load_i18n_strings(rag_schema_path)

        # 加载风格配置
        self.styles_config = self._load_styles_config()

        self.logger.info("NarrationGeneratorV2 initialized.")

    def _load_styles_config(self) -> Dict:
        style_path = self.metadata_dir / "styles.json"
        if style_path.is_file():
            with style_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        # 复用 V1 逻辑
        corpora = rag.list_corpora()
        corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)
        if not corpus:
            raise RuntimeError(f"RAG Corpus not found: '{corpus_display_name}'")
        return corpus

    def execute(self,
                series_name: str,
                corpus_display_name: str,
                blueprint_path: Path,  # [新增] Stage 2 必需
                config: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行三段式生成流程。
        Args:
            series_name: 剧集名称
            corpus_display_name: RAG 语料库名称
            blueprint_path: 本地蓝图 JSON 路径 (用于增强)
            config: 包含 'control_params', 'lang', 'model' 等的配置字典
        """
        self.logger.info(f"Starting V2 Generation for: {series_name}")

        # --- Stage 1: Intent-Based Retrieval ---
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        query = qb.build(series_name, config)

        rag_corpus = self._get_rag_corpus(corpus_display_name)
        top_k = config.get("rag_top_k", 50)

        self.logger.info(f"Retrieving from RAG (top_k={top_k})...")
        retrieval_response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
            text=query,
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
        )
        raw_chunks = retrieval_response.contexts.contexts

        # --- Stage 2: Timeline Alignment (Enhance) ---
        enhancer = ContextEnhancer(blueprint_path, self.logger)
        enhanced_context = enhancer.enhance(raw_chunks, config)

        if not enhanced_context or "No relevant scenes" in enhanced_context:
            self.logger.warning("Context enhancement resulted in empty content.")
            return {"narration_script": [], "metadata": {"status": "empty_context"}}

        # --- Stage 3: Synthesis (Style Injection) ---
        lang = config.get("lang", "zh")

        # 3.1 获取风格指令
        style_key = config.get("control_params", {}).get("style", "objective")
        # 获取对应语言的 styles，回退到 en
        lang_styles = self.styles_config.get(lang, self.styles_config.get("en", {}))
        style_instruction_text = lang_styles.get(style_key, lang_styles.get("objective", ""))

        system_instruction = f"""
【重要指令：角色与风格设定】
你现在的身份是：{style_instruction_text}
请忽略基础模版中关于“客观中立”的常规要求，必须严格按照上述身份的语气和口吻来生成解说词。
"""
        # 3.2 加载 User Prompt 模版
        # 注意：这里复用 V1 的模版文件，因为它只包含结构定义，内容由 {rag_context} 填充
        base_prompt = self._load_prompt_template(lang, "narration_generator")
        user_prompt = base_prompt.replace("{rag_context}", enhanced_context)

        # 3.3 拼接完整 Prompt
        full_prompt = system_instruction + "\n\n" + user_prompt

        # 3.4 推理
        self.logger.info(f"Invoking Gemini ({style_key})...")
        response_data, usage = self.gemini_processor.generate_content(
            model_name=config.get("model", "gemini-2.5-flash"),
            prompt=full_prompt,
            temperature=0.7  # 风格化需要较高的温度
        )

        return {
            "generation_date": datetime.now().isoformat(),
            "series_name": series_name,
            "config_snapshot": config,
            "narration_script": response_data.get("narration_script", []),
            "ai_total_usage": usage
        }