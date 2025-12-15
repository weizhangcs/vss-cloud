# ai_services/ai_platform/rag/data_manager.py

import logging
from typing import List
from vertexai import rag

logger = logging.getLogger(__name__)

class DataManager:
    """
    RAG 数据管理器。
    负责 Corpus 内的文件导入与管理。
    """

    def import_files(self, corpus_name: str, gcs_uris: List[str], chunk_size: int = 512, chunk_overlap: int = 50):
        """
        从 GCS 导入文件到指定的 Corpus。
        """
        if not gcs_uris:
            logger.warning("No GCS URIs provided for import.")
            return

        logger.info(f"📥 Importing {len(gcs_uris)} URIs into Corpus '{corpus_name}'...")
        try:
            response = rag.import_files(
                corpus_name,
                gcs_uris,
                transformation_config=rag.TransformationConfig(
                    chunking_config=rag.ChunkingConfig(
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap
                    )
                )
            )
            # 这里的 response 通常是 ImportRagFilesOperation，是一个 Long Running Operation
            logger.info(f"✅ Import operation initiated. Imported: {response.imported_rag_files_count} files.")
            return response
        except Exception as e:
            logger.error(f"❌ Failed to import files to '{corpus_name}': {e}")
            raise

    # TODO: 未来可在此添加 delete_file, list_files 等方法