# task_manager/handlers/rag.py

import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from .registry import HandlerRegistry
from .base import BaseTaskHandler

from ai_services.ai_platform.rag.deployer import RagDeployer
from ai_services.ai_platform.rag.schemas import RagTaskPayload  # [新增导入]

from core.exceptions import BizException
from core.error_codes import ErrorCode
from file_service.infrastructure.gcs_storage import upload_file_to_gcs
from ai_services.biz_services.narrative_dataset import NarrativeDataset


@HandlerRegistry.register(Task.TaskType.DEPLOY_RAG_CORPUS)
class RagDeploymentHandler(BaseTaskHandler):
    """
    RAG 部署任务处理器 (V6 Native & Schema-Driven)
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting RAG DEPLOYMENT for Task ID: {task.id}...")

        # --- [Step 1: Schema 校验] ---
        try:
            payload_obj = RagTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Task Payload: {e}")

        # 路径解析 (优先使用 precise key，回退到 legacy key)
        blueprint_str = payload_obj.absolute_blueprint_input_path or payload_obj.absolute_input_file_path
        if not blueprint_str:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg="Blueprint path is missing.")

        blueprint_path = Path(blueprint_str)
        facts_path = Path(payload_obj.absolute_facts_input_path)

        if not blueprint_path.exists():
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Blueprint not found: {blueprint_path}")
        if not facts_path.exists():
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Facts file not found: {facts_path}")

        # --- [Step 2: 预加载 Dataset 以获取元数据] ---
        # 我们需要在部署前拿到 asset_id，以便生成 Corpus Name
        try:
            with blueprint_path.open( encoding='utf-8') as f:
                raw_data = json.load(f)
            # 这里的加载也是一次“格式检查”
            dataset = NarrativeDataset(**raw_data)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid NarrativeDataset: {e}")

        # 确定 Asset ID (Dataset 中的元数据优先级最高，其次是 Payload 中的)
        asset_id = str(dataset.asset_uuid) if dataset.asset_uuid else payload_obj.asset_id
        if not asset_id:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR,
                               msg="Asset ID could not be determined from Dataset or Payload.")

        org_id = str(task.organization.org_id)
        corpus_display_name = f"{asset_id}-{org_id}"

        # 1. 初始化临时目录
        work_dir = settings.SHARED_LOG_ROOT / f"rag_task_{task.id}_debug_staging"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 3. 初始化 Deployer
        deployer = RagDeployer(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            logger=self.logger
        )

        # 加载 i18n
        i18n_path = settings.BASE_DIR / 'ai_services' / 'ai_platform' / 'rag' / 'metadata' / 'schemas.json'

        # 4. 执行部署
        try:
            deployer_result = deployer.execute(
                corpus_display_name=corpus_display_name,
                dataset_obj=dataset,  # [优化] 直接传入已加载的对象，避免重复 IO
                facts_path=facts_path,
                gcs_bucket_name=settings.GCS_DEFAULT_BUCKET,
                staging_dir=work_dir / "staging",
                org_id=org_id,
                asset_id=asset_id,
                i18n_schema_path=i18n_path,
                lang=payload_obj.lang
            )
        except Exception as e:
            raise BizException(ErrorCode.RAG_DEPLOYMENT_ERROR, msg=f"Deployer failed: {e}")

        # 备份源文件到 GCS
        self.logger.info(f"Backing up source files for Task {task.id} to GCS...")
        backup_prefix = f"archive/tasks/{task.id}/inputs"

        upload_file_to_gcs(
            local_file_path=blueprint_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/narrative_blueprint_NEW.json"
        )

        upload_file_to_gcs(
            local_file_path=facts_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/character_facts.json"
        )

        self.logger.info(f"RAG DEPLOYMENT finished successfully for Task ID: {task.id}.")
        return deployer_result