# 文件路径: ai_services/narration/narration_generator.py
# 描述: [重构后] 解说词生成器服务，已完全解耦。
# 版本: 2.0 (Decoupled & Reviewed)

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import vertexai
from vertexai import rag

# 导入项目内部依赖
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor


class NarrationGenerator(AIServiceMixin):
    """
    [重构后] 基于RAG知识库，生成带溯源信息的视频解说词。
    本服务已解耦，所有配置和依赖通过构造函数注入。
    """
    SERVICE_NAME = "narration_generator"

    def __init__(self,
                 project_id: str,
                 location: str,
                 prompts_dir: Path,
                 logger: logging.Logger,
                 work_dir: Path,
                 gemini_processor: GeminiProcessor):
        """
        初始化解说词生成器服务。

        Args:
            project_id (str): Google Cloud Project ID.
            location (str): Google Cloud Location (e.g., "us-central1").
            prompts_dir (Path): 包含此服务所需prompt模板的目录路径。
            logger (logging.Logger): 一个已配置好的日志记录器实例。
            work_dir (Path): 服务的工作目录，用于存储调试文件等。
            gemini_processor (GeminiProcessor): AI通信处理器实例。
        """
        # 核心依赖
        self.project_id = project_id
        self.location = location
        self.logger = logger
        self.work_dir = work_dir
        self.prompts_dir = prompts_dir
        self.gemini_processor = gemini_processor

        # 初始化Vertex AI
        vertexai.init(project=self.project_id, location=self.location)
        self.logger.info("NarrationGenerator Service initialized (decoupled).")

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        """获取对指定RAG语料库的引用，如果找不到则抛出异常。"""
        try:
            self.logger.info(f"正在查找RAG语料库: '{corpus_display_name}'...")
            corpora = rag.list_corpora()
            corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)
            if not corpus:
                raise RuntimeError(f"错误: 未能找到名为 '{corpus_display_name}' 的RAG语料库。")
            self.logger.info(f"成功连接到RAG语料库: {corpus.name}")
            return corpus
        except Exception as e:
            self.logger.error(f"连接RAG语料库时出错: {e}", exc_info=True)
            raise

    def execute(self, series_name: str, corpus_display_name: str, **kwargs) -> Dict[str, Any]:
        """
        为整个剧集执行解说词生成任务。

        Args:
            series_name (str): 剧集/故事的名称，用于生成查询。
            corpus_display_name (str): 要查询的目标RAG语料库的显示名称。
            **kwargs: 其他可选参数 (如 model, temp, lang, rag_top_k, debug)。

        Returns:
            Dict[str, Any]: 包含解说词脚本和元数据的字典。
        """
        try:
            rag_top_k = kwargs.get('rag_top_k', 200)

            # 步骤 1: 连接到RAG语料库
            rag_corpus = self._get_rag_corpus(corpus_display_name)

            # 步骤 2: 生成一个针对整个故事的宏观查询
            query_text = f"为剧集“{series_name}”生成一份完整的剧情解说词，请提供所有相关的场景资料。"

            # 步骤 3: 从RAG检索上下文
            retrieved_docs = self._query_rag_engine(rag_corpus, query_text, top_k=rag_top_k)
            if not retrieved_docs:
                self.logger.warning(f"未能为剧集 '{series_name}' 从RAG中检索到任何信息。")
                # 返回一个特定的结构体，而不是抛出异常
                return {"narration_script": [], "metadata": {"status": "skipped", "message": "No data found in RAG."}}

            context = self._assemble_context_from_retrievals(retrieved_docs)

            if kwargs.get('debug', False):
                debug_dir = self.work_dir / "_debug_artifacts"
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"{series_name}_narration_rag_context.txt").write_text(context, encoding='utf-8')

            # 步骤 4: 调用LLM生成结构化解说词
            self.logger.info("正在调用LLM生成解说词...")
            prompt = self._build_prompt(
                prompt_name='narration_generator',
                lang=kwargs.get('lang', 'zh'),
                rag_context=context
            )
            response_data, usage = self.gemini_processor.generate_content(
                model_name=kwargs.get('model', 'gemini-1.5-pro-latest'),
                prompt=prompt,
                temperature=kwargs.get('temp', 0.3)
            )
            self.logger.info("LLM已成功返回解说词数据。")

            # 步骤 5: 构建并返回最终结果
            final_output = {
                "generation_date": datetime.now().isoformat(),
                "series_name": series_name,
                "source_corpus": corpus_display_name,
                "narration_script": response_data.get("narration_script", []),
                "ai_total_usage": usage
            }
            return final_output

        except Exception as e:
            self.logger.critical(f"为 '{series_name}' 生成解说词时失败: {e}", exc_info=True)
            raise

    def _query_rag_engine(self, rag_corpus: Any, query: str, top_k: int) -> List[str]:
        """执行对Vertex AI RAG引擎的查询并返回文档内容。"""
        self.logger.info(f"正在向RAG引擎查询 (top_k={top_k}): '{query[:70]}...'")
        try:
            response = rag.retrieval_query(
                rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
                text=query,
                rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
            )
            all_chunks_text = [context.text for context in response.contexts.contexts]
            self.logger.info(f"成功从RAG检索到 {len(all_chunks_text)} 个相关文档片段。")
            return all_chunks_text
        except Exception as e:
            self.logger.error(f"查询RAG引擎时失败: {e}", exc_info=True)
            return []

    def _assemble_context_from_retrievals(self, snippets: List[str]) -> str:
        """将检索到的文档片段列表拼接成一个大的字符串上下文。"""
        separator = "\n\n" + "=" * 50 + "\n[下一个相关场景资料]\n" + "=" * 50 + "\n\n"
        return separator.join(snippets)