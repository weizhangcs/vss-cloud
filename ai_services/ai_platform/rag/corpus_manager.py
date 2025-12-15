# ai_services/ai_platform/rag/corpus_manager.py

import logging
from typing import Optional, List
from vertexai import rag
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)

class CorpusManager:
    """
    RAG 语料库资源管理器。
    负责 Corpus 的生命周期管理 (Create, Get, List, Delete)。
    """

    def create_corpus(self, display_name: str, description: str = "") -> rag.RagCorpus:
        """创建一个新的 RAG Corpus。"""
        try:
            corpus = rag.create_corpus(display_name=display_name, description=description)
            logger.info(f"✅ Created RAG Corpus: {corpus.name} (Display: {display_name})")
            return corpus
        except Exception as e:
            logger.error(f"❌ Failed to create corpus '{display_name}': {e}")
            raise

    def get_corpus_by_display_name(self, display_name: str) -> Optional[rag.RagCorpus]:
        """
        根据显示名称查找 Corpus。
        注意：Vertex AI 允许同名 Corpus，此方法返回找到的第一个，或者 None。
        """
        try:
            corpora = rag.list_corpora()
            for corpus in corpora:
                if corpus.display_name == display_name:
                    return corpus
            return None
        except Exception as e:
            logger.error(f"❌ Failed to list/get corpus '{display_name}': {e}")
            raise

    def list_corpora(self) -> List[rag.RagCorpus]:
        """列出当前项目下的所有 Corpus。"""
        try:
            return list(rag.list_corpora())
        except Exception as e:
            logger.error(f"❌ Failed to list corpora: {e}")
            raise

    def delete_corpus(self, corpus_name: str, force: bool = False):
        """
        删除指定的 Corpus。
        :param corpus_name: 资源名称 (e.g. projects/.../locations/.../ragCorpora/123)
        :param force: 是否强制删除（即使非空）。注意：Vertex SDK 可能不支持 force 参数，需依赖 SDK 行为。
        """
        try:
            # 目前 rag.delete_corpus(name) 是标准用法
            rag.delete_corpus(name=corpus_name)
            logger.info(f"🗑️ Deleted RAG Corpus: {corpus_name}")
        except google_exceptions.NotFound:
            logger.warning(f"⚠️ Corpus not found during deletion: {corpus_name}")
        except Exception as e:
            logger.error(f"❌ Failed to delete corpus '{corpus_name}': {e}")
            raise