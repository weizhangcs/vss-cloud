# ai_services/ai_platform/rag/retriever.py

import logging
from typing import List, Optional
from vertexai import rag

logger = logging.getLogger(__name__)

class RagRetriever:
    """
    RAG 检索器。
    封装 retrieval_query 逻辑，支持 Reranking 配置预留。
    """

    def retrieve(self,
                 corpus_name: str,
                 query: str,
                 top_k: int = 10,
                 enable_reranking: bool = False,
                 rerank_top_n: int = 5) -> List[rag.RagContext]:
        """
        执行检索。

        Args:
            corpus_name: RAG Corpus 资源名称。
            query: 用户查询文本。
            top_k: 返回的上下文数量。
            enable_reranking: [预留] 是否开启重排 (默认为 False)。
            rerank_top_n: [预留] 重排后返回的 Top N 数量。

        Returns:
            List[rag.RagContext]: 检索到的上下文列表。
        """
        logger.info(f"🔍 Retrieving from '{corpus_name}' | Query: {query[:30]}... | Top_k: {top_k}")

        try:
            # 1. 基础检索配置
            retrieval_config = rag.RagRetrievalConfig(top_k=top_k)

            # 2. [预留] Reranking 逻辑接入点
            if enable_reranking:
                # TODO: 当需要启用重排时，在此处配置 ranking config。
                # 目前 Google Vertex AI SDK 的 ranking 参数配置方式可能会更新，
                # 暂时保持 False，逻辑透明透传。
                logger.info(f"ℹ️ Reranking is enabled (Placeholder). Rerank Top N: {rerank_top_n}")
                # 示例代码 (视 SDK 版本而定):
                # retrieval_config.ranking = rag.RankingConfig(model_name="semantic-ranker-512", top_n=rerank_top_n)
                pass

            # 3. 执行检索
            response = rag.retrieval_query(
                rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
                text=query,
                rag_retrieval_config=retrieval_config
            )

            contexts = response.contexts.contexts
            logger.info(f"✅ Retrieved {len(contexts)} contexts.")
            return contexts

        except Exception as e:
            logger.error(f"❌ Retrieval failed: {e}", exc_info=True)
            raise