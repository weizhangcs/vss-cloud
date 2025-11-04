# task_manager/rag_deployment/deployer.py

import json
from collections import defaultdict
from pathlib import Path
import logging # <-- 使用标准日志库

# 从我们新创建的schemas模块导入
from .schemas import NarrativeBlueprint, IdentifiedFact

import vertexai
from vertexai import rag
from google.cloud import storage
from django.conf import settings # <-- 导入Django settings

# 获取一个日志记录器实例
logger = logging.getLogger(__name__)

class RagDeployer:
    """
    一个被改造后、适配Django Celery环境的RAG部署器。
    """
    def __init__(self):
        """
        初始化时，直接从Django settings加载配置。
        """
        self.project_id = settings.GOOGLE_CLOUD_PROJECT
        self.location = settings.GOOGLE_CLOUD_LOCATION

        # 初始化Vertex AI
        vertexai.init(project=self.project_id, location=self.location)
        logger.info(f"RagDeployer initialized for project '{self.project_id}' in '{self.location}'")

    def execute(self, instance_id: str, blueprint_path: Path, facts_path: Path, gcs_bucket: str, corpus_basename: str,
                staging_dir: Path):
        """执行完整的部署流程。"""
        corpus_full_name = f"{corpus_basename}-{instance_id}"
        logger.info("=" * 20 + f" 🚀 RAG 部署任务启动 (实例: {instance_id}) 🚀 " + "=" * 20)

        try:
            gcs_uri = self._fuse_and_prepare_files(
                source_blueprint_path=blueprint_path,
                enhanced_facts_path=facts_path,
                staging_dir=staging_dir,
                gcs_bucket_name=gcs_bucket,
                instance_id=instance_id
            )

            self._upload_dir_to_gcs(
                local_dir=staging_dir,
                gcs_uri=gcs_uri,
            )

            self._deploy_to_rag_engine(
                corpus_full_name=corpus_full_name,
                gcs_uri=gcs_uri
            )

            logger.info("=" * 70)
            logger.info(f"✅ 实例 '{instance_id}' 的RAG部署任务已成功启动！")
            logger.info(f"   目标语料库: {corpus_full_name}")
            logger.info("   请前往Google Cloud控制台查看文件导入进度。")
            logger.info("=" * 70 + "\n")

        except Exception as e:
            self.logger.critical(f"部署流程发生严重错误: {e}", exc_info=True)
            # 可以在这里决定是否要重新抛出异常
            # raise

    def _fuse_and_prepare_files(self, source_blueprint_path: Path, enhanced_facts_path: Path, staging_dir: Path,
                                gcs_bucket_name: str, instance_id: str) -> str:
        logger.info(f"▶️ 步骤 1/4: 正在加载实例 '{instance_id}' 的源数据...")
        try:
            blueprint = NarrativeBlueprint.parse_file(source_blueprint_path)
            with enhanced_facts_path.open('r', encoding='utf-8') as f:
                facts_data = json.load(f)
            all_facts = []
            facts_by_character_map = facts_data.get("identified_facts_by_character", {})
            for char_name, facts_list in facts_by_character_map.items():
                for fact_dict in facts_list:
                    fact_dict_with_owner = {**fact_dict, "character_name": char_name}
                    all_facts.append(IdentifiedFact(**fact_dict_with_owner))
            logger.info("✅ 源数据与增强事实加载并校验成功。")
        except Exception as e:
            self.logger.error(f"❌ 严重错误: 加载文件时失败。\n   具体错误: {e}")
            raise e

        logger.info("▶️ 步骤 2/4: 正在融合增强事实...")
        facts_by_scene = defaultdict(list)
        for fact in all_facts:
            facts_by_scene[str(fact.scene_id)].append(fact)
        for scene_id, scene_obj in blueprint.scenes.items():
            if scene_id in facts_by_scene:
                scene_obj.enhanced_facts = facts_by_scene[scene_id]
        logger.info("✅ 数据融合完成。")

        staging_dir.mkdir(parents=True, exist_ok=True)
        series_id = blueprint.project_metadata.project_name
        logger.info(f"▶️ 步骤 3/4: 正在为 '{series_id}' 生成富文本文件...")
        for scene_id, scene_obj in blueprint.scenes.items():
            rich_text_content = scene_obj.to_rag_b_text(series_id=series_id, lang='zh')
            scene_file_path = staging_dir / f"{series_id}_scene_{scene_id}_enhanced.txt"
            scene_file_path.write_text(rich_text_content, encoding='utf-8')
        logger.info(f"✅ 富文本文档已在本地暂存目录 '{staging_dir}' 生成。")

        gcs_uri = f"gs://{gcs_bucket_name}/rag-engine-source/{instance_id}/{series_id}"
        return gcs_uri

    def _upload_dir_to_gcs(self, local_dir: Path, gcs_uri: str):
        bucket_name = gcs_uri.split("/")[2]
        gcs_prefix = "/".join(gcs_uri.split("/")[3:])
        logger.info(f"▶️ 步骤 4/4: 正在将暂存目录上传到 GCS 路径: '{gcs_uri}'...")
        try:
            storage_client = storage.Client(project=self.project_id)
            bucket = storage_client.bucket(bucket_name)
            for local_file in local_dir.glob("*.txt"):
                blob = bucket.blob(f"{gcs_prefix}/{local_file.name}")
                blob.upload_from_filename(str(local_file))
            logger.info(f"✅ 所有文件上传成功！")
        except Exception as e:
            self.logger.error(f"❌ 错误: 上传到GCS失败: {e}")
            raise e

    def _deploy_to_rag_engine(self, corpus_full_name: str, gcs_uri: str):
        logger.info(f"▶️ [最终步骤]: 正在向RAG语料库 '{corpus_full_name}' 同步数据...")
        try:
            corpora = rag.list_corpora()
            rag_corpus = next((c for c in corpora if c.display_name == corpus_full_name), None)
            if not rag_corpus:
                logger.info(f"   未找到语料库 '{corpus_full_name}'。正在创建新的语料库...")
                rag_corpus = rag.create_corpus(display_name=corpus_full_name)
                logger.info("✅ 新语料库创建成功。")
            else:
                logger.info("✅ RAG语料库已存在。")
            logger.info(f"   正在从 GCS URI: {gcs_uri} 发起文件导入请求...")
            rag.import_files(
                rag_corpus.name,
                [gcs_uri],
                transformation_config=rag.TransformationConfig(
                    chunking_config=rag.ChunkingConfig(chunk_size=512, chunk_overlap=50)
                )
            )
            logger.info("✅ 文件导入请求已成功发起。")
        except Exception as e:
            self.logger.error(f"❌ 错误: 处理RAG语料库时失败: {e}")
            raise e