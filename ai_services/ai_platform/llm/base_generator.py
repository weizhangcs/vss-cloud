# ai_services/common/base_generator.py

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List

import vertexai
from vertexai import rag
from pydantic import BaseModel

from .mixins import AIServiceMixin
from .gemini_processor import GeminiProcessor
from .cost_calculator import CostCalculator


class BaseRagGenerator(AIServiceMixin, ABC):
    """
    [Base Engine] 通用 RAG 生成服务基类。
    """

    def __init__(self,
                 project_id: str,
                 location: str,
                 logger: logging.Logger,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator,  # [New] 强制依赖注入
                 **kwargs):
        self.project_id = project_id
        self.location = location
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator  # [New] 保存实例

        for k, v in kwargs.items():
            setattr(self, k, v)

        try:
            vertexai.init(project=self.project_id, location=self.location)
        except Exception as e:
            self.logger.error(f"Vertex AI initialization failed: {e}")
            raise

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        try:
            corpora = rag.list_corpora()
            corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)
            if not corpus:
                raise RuntimeError(f"RAG Corpus not found: '{corpus_display_name}'")
            return corpus
        except Exception as e:
            self.logger.error(f"Failed to list/find RAG corpus: {e}")
            raise

    def _retrieve_from_rag(self, corpus_name: str, query: str, top_k: int) -> List[Any]:
        self.logger.info(f"Retrieving from RAG (top_k={top_k})...")
        try:
            retrieval_response = rag.retrieval_query(
                rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
                text=query,
                rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
            )
            return retrieval_response.contexts.contexts
        except Exception as e:
            self.logger.error(f"RAG Retrieval failed: {e}")
            raise RuntimeError(f"RAG Service Error: {e}")

    def execute(self,
                asset_name: str,
                corpus_display_name: str,
                config: Dict[str, Any],
                **kwargs) -> Dict[str, Any]:

        self.logger.info(f"Starting Generation Pipeline for: {asset_name}")

        # Step 1: Config
        config_with_context = config.copy()
        config_with_context['asset_name'] = asset_name
        service_config = self._validate_config(config_with_context)

        # Step 2: Query
        query = self._build_query(service_config)

        # Step 3: Retrieval
        top_k = getattr(service_config, 'rag_top_k', 50)
        rag_corpus = self._get_rag_corpus(corpus_display_name)
        raw_chunks = self._retrieve_from_rag(rag_corpus.name, query, top_k)

        if not raw_chunks:
            raise RuntimeError(f"RAG retrieval returned 0 chunks for query: '{query}'.")

        # Step 4: Context
        final_context = self._prepare_context(raw_chunks, service_config, **kwargs)
        if not final_context:
            raise RuntimeError("Context preparation resulted in empty content.")

        # Step 5: Prompt
        prompt = self._construct_prompt(final_context, service_config)

        # Step 6: Inference
        model_name = getattr(service_config, 'model', 'gemini-2.5-flash')
        temp = getattr(service_config, 'temperature', 0.7)
        if hasattr(service_config, 'temp'): temp = service_config.temp

        self.logger.info(f"Invoking LLM (Model: {model_name})...")
        response_data, usage = self.gemini_processor.generate_content(
            model_name=model_name,
            prompt=prompt,
            temperature=temp
        )

        # [New] Step 6.5: Cost Calculation
        # 计算本次调用的金额，并合并到 usage 字典中
        try:
            costs = self.cost_calculator.calculate(model_name, usage)
            usage.update(costs)
            self.logger.info(f"Inference Cost: ${costs.get('cost_usd', 0.0):.4f}")
        except Exception as e:
            self.logger.warning(f"Failed to calculate costs: {e}")

        # --- Step 7: 结果解析与后处理 ---
        # [核心修改] 增加 rag_context=final_context 参数
        # 这样子类的 _post_process 就能拿到刚才生成的上下文了
        final_result_dict = self._post_process(
            response_data,
            service_config,
            usage,
            corpus_display_name=corpus_display_name,
            rag_context=final_context,  # <--- 新增这一行
            **kwargs
        )

        return final_result_dict

    # ... (Abstract Hooks 保持不变) ...
    @abstractmethod
    def _validate_config(self, config: Dict[str, Any]) -> BaseModel:
        pass

    @abstractmethod
    def _build_query(self, config: BaseModel) -> str:
        pass

    @abstractmethod
    def _prepare_context(self, raw_chunks: List[Any], config: BaseModel, **kwargs) -> str:
        pass

    @abstractmethod
    def _construct_prompt(self, context: str, config: BaseModel) -> str:
        pass

    @abstractmethod
    def _post_process(self, llm_response: Dict, config: BaseModel, usage: Dict, **kwargs) -> Dict:
        pass