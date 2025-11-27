from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.rag.deployer import RagDeployer
from ai_services.rag.schemas import load_i18n_strings
from utils.gcs_utils import upload_file_to_gcs
from .base import BaseTaskHandler
from .registry import HandlerRegistry


@HandlerRegistry.register(Task.TaskType.DEPLOY_RAG_CORPUS)
class RagDeploymentHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting RAG DEPLOYMENT for Task ID: {task.id}...")

        payload = task.payload
        blueprint_path_str = payload.get("absolute_blueprint_input_path")
        facts_path_str = payload.get("absolute_facts_input_path")

        if not all([blueprint_path_str, facts_path_str]):
            raise ValueError("Payload for DEPLOY_RAG_CORPUS is missing required absolute paths.")

        local_blueprint_path = Path(blueprint_path_str)
        local_facts_path = Path(facts_path_str)

        # 临时目录
        temp_dir = settings.SHARED_TMP_ROOT / f"rag_deploy_{task.id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # 租户与资产标识
        org_id = str(task.organization.org_id)
        asset_id = payload.get("asset_id")
        if not asset_id:
            raise ValueError("Payload missing required 'asset_id'.")

        # 构建 Corpus Name
        corpus_display_name = f"{asset_id}-{org_id}"

        # 初始化 Deployer
        deployer = RagDeployer(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            logger=self.logger
        )

        # 加载 i18n
        i18n_path = settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json'
        load_i18n_strings(i18n_path)

        # 执行部署
        deployer_result = deployer.execute(
            corpus_display_name=corpus_display_name,
            blueprint_path=local_blueprint_path,
            facts_path=local_facts_path,
            gcs_bucket_name=settings.GCS_DEFAULT_BUCKET,
            staging_dir=temp_dir / "staging",
            org_id=org_id,
            asset_id=asset_id
        )

        # 备份源文件到 GCS
        self.logger.info(f"Backing up source files for Task {task.id} to GCS...")
        backup_prefix = f"archive/tasks/{task.id}/inputs"
        upload_file_to_gcs(
            local_file_path=local_blueprint_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/narrative_blueprint.json"
        )
        upload_file_to_gcs(
            local_file_path=local_facts_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/character_facts.json"
        )

        self.logger.info(f"RAG DEPLOYMENT finished successfully for Task ID: {task.id}.")
        return deployer_result