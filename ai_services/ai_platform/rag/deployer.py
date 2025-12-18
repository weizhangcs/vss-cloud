# ai_services/ai_platform/rag/deployer.py

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import List

import vertexai
from google.api_core import exceptions as google_exceptions
from core.exceptions import RateLimitException

# [Import 修正]
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from .corpus_manager import CorpusManager
from .data_manager import DataManager
from .schemas import IdentifiedFact, RagContentFormatter, CharacterFactsFile

from file_service.infrastructure.gcs_storage import upload_directory_to_gcs


class RagDeployer:
    """
    RAG 部署器服务 (V6 Dataset Adapter 版)
    """

    def __init__(self, project_id: str, location: str, logger: logging.Logger):
        self.project_id = project_id
        self.location = location
        self.logger = logger

        try:
            vertexai.init(project=self.project_id, location=self.location)
            self.corpus_manager = CorpusManager()
            self.data_manager = DataManager()
            self.logger.info(f"RagDeployer initialized (Project: {project_id})")
        except Exception as e:
            self.logger.error(f"Vertex AI initialization failed: {e}", exc_info=True)
            raise

    def execute(self,
                corpus_display_name: str,
                dataset_obj: NarrativeDataset,
                facts_path: Path,
                gcs_bucket_name: str,
                staging_dir: Path,
                org_id: str,
                asset_id: str,
                i18n_schema_path: Path,  # [新增] 接收 Schema 路径
                lang: str = 'zh'
                ):

        self.logger.info("=" * 20 + f" 🚀 RAG 部署任务启动 (V6) " + "=" * 20)

        # [Step 0: 加载 i18n 配置]
        # 这里由 Deployer 负责加载资源，职责归属更清晰
        i18n_labels = {}
        try:
            with i18n_schema_path.open('r', encoding='utf-8') as f:
                full_i18n = json.load(f)
                i18n_labels = full_i18n.get(lang, full_i18n.get('en', {}))
        except Exception as e:
            self.logger.warning(f"Failed to load i18n labels from {i18n_schema_path}: {e}")
            # 可以在此定义兜底字典，或者依赖 Formatter 的 defaults

        try:
            # 1. 融合与准备文件
            gcs_uri, total_scenes = self._prepare_rag_documents(
                dataset=dataset_obj,
                enhanced_facts_path=facts_path,
                staging_dir=staging_dir,
                gcs_bucket_name=gcs_bucket_name,
                org_id=org_id,
                asset_id=asset_id,
                labels=i18n_labels
            )

            # 2. 上传 GCS
            self._upload_dir_to_gcs(staging_dir, gcs_uri)

            # 3. 部署到 Vertex AI
            self._deploy_to_rag_engine(corpus_display_name, gcs_uri)

            self.logger.info(f"✅ RAG 部署成功完成。")
            return {
                "message": "RAG deployment initiated.",
                "corpus_name": corpus_display_name,
                "source_gcs_uri": gcs_uri,
                "total_scene_count": total_scenes
            }

        except Exception as e:
            if isinstance(e, (google_exceptions.TooManyRequests, google_exceptions.ResourceExhausted)):
                raise RateLimitException(msg=str(e), provider="GoogleVertexAI") from e
            self.logger.critical(f"部署流程发生错误: {e}", exc_info=True)
            raise

    def _prepare_rag_documents(self, dataset: NarrativeDataset, enhanced_facts_path: Path,
                               staging_dir: Path, gcs_bucket_name: str,
                               org_id: str, asset_id: str,
                               labels: dict) -> tuple[str, int]:

        self.logger.info("▶️ 步骤 1/4: 加载 Facts 并与 Dataset 融合...")

        # A. 加载 Facts (使用 Schema 校验)
        try:
            with enhanced_facts_path.open('r', encoding='utf-8') as f:
                facts_raw = json.load(f)
            # [Validation] 确保 Facts 文件格式正确
            facts_file = CharacterFactsFile(**facts_raw)
        except Exception as e:
            raise ValueError(f"Failed to load facts file: {e}")

        # B. 整理 Facts (按 character 归类 -> 打散到 scene)
        # Map: { scene_id(int): [IdentifiedFact, ...] }
        facts_by_scene = defaultdict(list)
        count_facts = 0

        # facts_file.identified_facts_by_character 是一个 Dict[str, List[Dict]]
        for char_name, facts_list in facts_file.identified_facts_by_character.items():
            for fact_dict in facts_list:
                # 注入归属人
                fact_dict['character_name'] = char_name
                try:
                    # 使用 IdentifiedFact Schema 再次校验单条数据
                    fact_obj = IdentifiedFact(**fact_dict)
                    facts_by_scene[fact_obj.scene_id].append(fact_obj)
                    count_facts += 1
                except Exception as e:
                    self.logger.warning(f"Skipping invalid fact for {char_name}: {e}")

        self.logger.info(f"✅ 数据融合完成。Scenes: {len(dataset.scenes)}, Facts: {count_facts}")

        # C. 生成富文本 (Formatting)
        staging_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"▶️ 步骤 2/4: 生成 RAG 富文本文档...")

        for scene in dataset.scenes.values():
            # 获取该场景对应的 Facts
            scene_facts = facts_by_scene.get(scene.local_id, [])

            # 使用 Formatter 生成文本
            rich_text = RagContentFormatter.format_scene(
                scene=scene,
                facts=scene_facts,
                asset_id=asset_id,
                labels=labels
            )

            # D. 写入文件
            filename = f"{asset_id}_scene_{scene.local_id}_enhanced.txt"
            (staging_dir / filename).write_text(rich_text, encoding='utf-8')

        gcs_uri = f"gs://{gcs_bucket_name}/rag-engine-source/{org_id}/{asset_id}"
        return gcs_uri, len(dataset.scenes)

    def _upload_dir_to_gcs(self, local_dir: Path, gcs_uri: str):
        # ... (保持原代码不变，这是通用的) ...
        parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        self.logger.info(f"▶️ 步骤 3/4: 上传至 GCS: {gcs_uri}")
        upload_directory_to_gcs(local_dir, bucket, prefix)

    def _deploy_to_rag_engine(self, corpus_name: str, gcs_uri: str):
        # ... (保持原代码不变) ...
        self.logger.info(f"▶️ 步骤 4/4: RAG Engine 同步...")
        corpus = self.corpus_manager.get_corpus_by_display_name(corpus_name)
        if not corpus:
            corpus = self.corpus_manager.create_corpus(corpus_name)

        self.data_manager.import_files(corpus.name, [gcs_uri])