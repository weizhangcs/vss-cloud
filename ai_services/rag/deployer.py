# 文件路径: ai_services/rag/deployer.py
# 描述: [重构后] RAG部署器服务，已完全解耦，通过依赖注入模式运行。
# 版本: 2.0 (Decoupled & Reviewed)

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Union

import vertexai
from vertexai import rag
from google.cloud import storage

# 从同级目录的 schemas.py 导入Pydantic模型
from .schemas import NarrativeBlueprint, IdentifiedFact


class RagDeployer:
    """
    RAG部署器服务 (RAG Deployer Service)。

    本服务负责将融合了增强事实的剧本数据，处理成RAG引擎所需的富文本文档，
    上传至Google Cloud Storage (GCS)，并触发Vertex AI RAG引擎的文件同步。

    设计原则:
    - **解耦**: 不直接依赖任何框架（如Django）。所有配置（项目ID、密钥、路径）均通过依赖注入传入。
    - **职责单一**: 专注于“部署RAG语料库”这一核心任务。
    - **幂等性**: 能够处理语料库已存在（更新）和不存在（创建）两种情况。
    """

    def __init__(self, project_id: str, location: str, logger: logging.Logger):
        """
        初始化RAG部署器。

        Args:
            project_id (str): Google Cloud 项目ID。
            location (str): Google Cloud 区域 (e.g., "us-central1")。
            logger (logging.Logger): 一个由外部调用方传入的、已配置好的日志记录器实例。
        """
        self.project_id = project_id
        self.location = location
        self.logger = logger

        # 初始化Vertex AI SDK，只需要在服务实例化时执行一次。
        try:
            vertexai.init(project=self.project_id, location=self.location)
            self.logger.info(f"RagDeployer initialized for project '{self.project_id}' in '{self.location}'")
        except Exception as e:
            self.logger.error(f"Vertex AI initialization failed: {e}", exc_info=True)
            raise

    def execute(self,
                corpus_display_name: str,
                blueprint_path: Path,
                facts_path: Path,
                gcs_bucket_name: str,
                staging_dir: Path,
                org_id: str,  # [修改] instance_id -> org_id
                asset_id: str):
        """
        执行完整的部署流程。

        此方法编排了从数据融合到最终触发RAG引擎同步的全部步骤。

        Args:
            corpus_display_name (str): RAG语料库的目标显示名称。这是实现租户隔离的关键，
                                       通常由 "series_id" 和 "instance_id" 拼接而成。
            blueprint_path (Path): 本地临时目录中 narrative_blueprint.json 文件的路径。
            facts_path (Path): 本地临时目录中 character_facts.json 文件的路径。
            gcs_bucket_name (str): 用于暂存RAG源文件的GCS桶名称。
            staging_dir (Path): 用于在本地生成富文本文档的临时目录。
            instance_id (str): 租户实例ID，用于在GCS中创建隔离的文件夹结构。

        Returns:
            Dict: 一个包含部署结果信息的字典，用于Celery Task记录。
        """
        self.logger.info("=" * 20 + f" 🚀 RAG 部署任务启动 (Corpus: {corpus_display_name}) 🚀 " + "=" * 20)

        try:
            # 步骤 1 & 2: 在本地融合数据并生成RAG所需的富文本文件，同时构建GCS的目标URI。
            gcs_uri, total_scenes = self._fuse_and_prepare_files(
                source_blueprint_path=blueprint_path,
                enhanced_facts_path=facts_path,
                staging_dir=staging_dir,
                gcs_bucket_name=gcs_bucket_name,
                org_id=org_id,
                asset_id=asset_id
            )

            # 步骤 3: 将本地生成的富文本文件批量上传到GCS。
            self._upload_dir_to_gcs(
                local_dir=staging_dir,
                gcs_uri=gcs_uri,
            )

            # 步骤 4: 指示Vertex AI RAG引擎从GCS拉取并同步文件。
            self._deploy_to_rag_engine(
                corpus_display_name=corpus_display_name,
                gcs_uri=gcs_uri
            )

            self.logger.info("=" * 70)
            self.logger.info(f"✅ 租户 '{org_id}' 的RAG部署任务已成功启动！")
            self.logger.info(f"   目标语料库: {corpus_display_name}")
            self.logger.info("   请前往Google Cloud控制台查看文件导入进度。")
            self.logger.info("=" * 70 + "\n")

            # 返回关键信息，以便Celery Task存入最终的任务结果中。
            return {
                "message": "RAG deployment process initiated successfully.",
                "corpus_name": corpus_display_name,
                "source_gcs_uri": gcs_uri,
                "total_scene_count": total_scenes
            }

        except Exception as e:
            self.logger.critical(f"部署流程发生严重错误: {e}", exc_info=True)
            raise  # 重新抛出异常，让Celery知道任务失败

    def _fuse_and_prepare_files(self, source_blueprint_path: Path, enhanced_facts_path: Path, staging_dir: Path,
                                gcs_bucket_name: str, org_id: str, asset_id: str) -> tuple[str, int]:
        """在本地处理文件：加载、融合、生成富文本。"""
        self.logger.info(f"▶️ 步骤 1/4: 正在加载租户 '{org_id}' 的源数据......")
        try:
            # 使用Pydantic模型加载和验证输入文件，确保数据结构正确。
            blueprint = NarrativeBlueprint.parse_file(source_blueprint_path)
            with enhanced_facts_path.open('r', encoding='utf-8') as f:
                facts_data = json.load(f)

            # 将扁平的facts列表转换为Pydantic对象，并注入事实的归属者（character_name）。
            all_facts = []
            facts_by_character_map = facts_data.get("identified_facts_by_character", {})
            for char_name, facts_list in facts_by_character_map.items():
                for fact_dict in facts_list:
                    fact_dict_with_owner = {**fact_dict, "character_name": char_name}
                    all_facts.append(IdentifiedFact(**fact_dict_with_owner))
            self.logger.info("✅ 源数据与增强事实加载并校验成功。")
            total_scenes = len(blueprint.scenes)

        except Exception as e:
            self.logger.error(f"❌ 严重错误: 加载或解析文件时失败。\n   具体错误: {e}", exc_info=True)
            raise

        self.logger.info("▶️ 步骤 2/4: 正在将增强事实融合到剧本场景中...")
        facts_by_scene = defaultdict(list)
        for fact in all_facts:
            facts_by_scene[str(fact.scene_id)].append(fact)

        for scene_id, scene_obj in blueprint.scenes.items():
            if scene_id in facts_by_scene:
                scene_obj.enhanced_facts = facts_by_scene[scene_id]
        self.logger.info("✅ 数据融合完成。")

        # 确保本地暂存目录存在。
        staging_dir.mkdir(parents=True, exist_ok=True)
        project_name = blueprint.project_metadata.project_name
        self.logger.info(f"▶️ 步骤 3/4: 正在为 '{project_name}' (Asset: {asset_id}) 生成富文本文件...")

        # 遍历每个场景，调用Pydantic模型的方法生成RAG所需的富文本内容。
        for scene_id, scene_obj in blueprint.scenes.items():
            # [核心修改] 传入 asset_id (UUID) 作为 RAG 文档的元数据
            rich_text_content = scene_obj.to_rag_text(asset_id=asset_id, lang='zh')

            # [核心修改] 文件名使用 asset_id 确保唯一性和稳定性
            # 格式: {asset_id}_scene_{scene_id}_enhanced.txt
            scene_file_path = staging_dir / f"{asset_id}_scene_{scene_id}_enhanced.txt"
            scene_file_path.write_text(rich_text_content, encoding='utf-8')
        self.logger.info(f"✅ 富文本文档已在本地暂存目录 '{staging_dir}' 生成。")

        # 构建并返回GCS的目标URI，用于后续的上传和RAG同步。
        gcs_uri = f"gs://{gcs_bucket_name}/rag-engine-source/{org_id}/{asset_id}"
        return gcs_uri, total_scenes

    def _upload_dir_to_gcs(self, local_dir: Path, gcs_uri: str):
        """将本地目录中的所有.txt文件上传到指定的GCS路径。"""
        bucket_name = gcs_uri.split("/")[2]
        gcs_prefix = "/".join(gcs_uri.split("/")[3:])
        self.logger.info(f"▶️ 步骤 4/4: 正在将暂存目录上传到 GCS 路径: '{gcs_uri}'...")
        try:
            storage_client = storage.Client(project=self.project_id)
            bucket = storage_client.bucket(bucket_name)

            # 遍历本地暂存目录中的所有txt文件并上传。
            for local_file in local_dir.glob("*.txt"):
                blob = bucket.blob(f"{gcs_prefix}/{local_file.name}")
                blob.upload_from_filename(str(local_file))
            self.logger.info(f"✅ 所有文件上传成功！")
        except Exception as e:
            self.logger.error(f"❌ 错误: 上传到GCS失败: {e}", exc_info=True)
            raise

    def _deploy_to_rag_engine(self, corpus_display_name: str, gcs_uri: str):
        """创建或更新RAG语料库，并从GCS导入文件。"""
        self.logger.info(f"▶️ [最终步骤]: 正在向RAG语料库 '{corpus_display_name}' 同步数据...")
        try:
            # 幂等性检查：首先列出所有语料库，查找是否存在同名实例。
            corpora = rag.list_corpora()
            rag_corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)

            # 如果语料库不存在，则创建一个新的。
            if not rag_corpus:
                self.logger.info(f"   未找到语料库 '{corpus_display_name}'。正在创建新的语料库...")
                rag_corpus = rag.create_corpus(display_name=corpus_display_name)
                self.logger.info("✅ 新语料库创建成功。")
            else:
                self.logger.info("✅ RAG语料库已存在，将进行文件同步/更新。")

            # 发起文件导入请求。这是一个异步操作，API会立即返回。
            # RAG引擎会在后台从GCS拉取、解析、分块并索引文件。
            self.logger.info(f"   正在从 GCS URI: {gcs_uri} 发起文件导入请求...")
            rag.import_files(
                rag_corpus.name,
                [gcs_uri],
                transformation_config=rag.TransformationConfig(
                    chunking_config=rag.ChunkingConfig(chunk_size=512, chunk_overlap=50)
                )
            )
            self.logger.info("✅ 文件导入请求已成功发起。")
        except Exception as e:
            self.logger.error(f"❌ 错误: 处理RAG语料库时失败: {e}", exc_info=True)
            raise