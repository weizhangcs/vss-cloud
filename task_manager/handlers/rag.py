import json  # [新增] 用于处理 JSON 读写
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.ai_platform.rag.deployer import RagDeployer
from ai_services.ai_platform.rag.schemas import load_i18n_strings
from file_service.infrastructure.gcs_storage import upload_file_to_gcs
from .base import BaseTaskHandler
from .registry import HandlerRegistry
# [新增导入] 适配层和异常处理
from core.exceptions import BizException
from core.error_codes import ErrorCode
from ai_services.utils.blueprint_converter import BlueprintConverter, Blueprint  # 假设路径


@HandlerRegistry.register(Task.TaskType.DEPLOY_RAG_CORPUS)
class RagDeploymentHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting RAG DEPLOYMENT for Task ID: {task.id}...")

        payload = task.payload
        blueprint_path_str = payload.get("absolute_blueprint_input_path")
        facts_path_str = payload.get("absolute_facts_input_path")

        if not all([blueprint_path_str, facts_path_str]):
            raise ValueError("Payload for DEPLOY_RAG_CORPUS is missing required absolute paths.")

        # 1. 初始化临时目录
        temp_dir = settings.SHARED_TMP_ROOT / f"rag_deploy_{task.id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # --- [核心利旧适配层] 转换新的 Blueprint 格式为旧版 ---
        new_blueprint_path = Path(blueprint_path_str)
        if not new_blueprint_path.is_file():
            raise FileNotFoundError(f"Input blueprint file not found: {new_blueprint_path}")

        # a. 读取新 Blueprint JSON
        with new_blueprint_path.open('r', encoding='utf-8') as f:
            new_blueprint_json = json.load(f)

        # b. 校验并解析为新 Pydantic 对象
        try:
            new_blueprint_obj = Blueprint.model_validate(new_blueprint_json)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"New blueprint file validation failed: {e}")

        # c. 实例化并执行转换
        converter = BlueprintConverter()
        old_blueprint_dict = converter.convert(new_blueprint_obj)

        # d. 保存转换后的旧版 Blueprint 到临时文件
        converted_filename = f"narrative_blueprint_OLD_CONVERTED.json"
        converted_path = temp_dir / converted_filename

        with converted_path.open('w', encoding='utf-8') as f:
            json.dump(old_blueprint_dict, f, ensure_ascii=False, indent=2)

        # e. 覆盖 Blueprint 路径：让下游服务使用转换后的文件
        local_blueprint_path = converted_path
        # --------------------------------------------------------

        local_facts_path = Path(facts_path_str)

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

        # 加载 i18n (保持不变)
        i18n_path = settings.BASE_DIR / 'ai_services' / 'ai_platform' / 'rag' / 'metadata' / 'schemas.json'
        load_i18n_strings(i18n_path)

        # 执行部署 (使用转换后的 local_blueprint_path)
        deployer_result = deployer.execute(
            corpus_display_name=corpus_display_name,
            blueprint_path=local_blueprint_path,  # <-- 使用转换后的路径
            facts_path=local_facts_path,
            gcs_bucket_name=settings.GCS_DEFAULT_BUCKET,
            staging_dir=temp_dir / "staging",
            org_id=org_id,
            asset_id=asset_id
        )

        # 备份源文件到 GCS
        self.logger.info(f"Backing up source files for Task {task.id} to GCS...")
        backup_prefix = f"archive/tasks/{task.id}/inputs"

        # 备份原始的 New Blueprint 文件，用于溯源
        upload_file_to_gcs(
            local_file_path=new_blueprint_path,  # 原始输入文件
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/narrative_blueprint_NEW.json"
        )

        # 备份转换后的 Old Blueprint 文件，用于调试
        upload_file_to_gcs(
            local_file_path=converted_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/{converted_filename}"
        )

        # 备份 facts 文件
        upload_file_to_gcs(
            local_file_path=local_facts_path,
            bucket_name=settings.GCS_DEFAULT_BUCKET,
            gcs_object_name=f"{backup_prefix}/character_facts.json"
        )

        self.logger.info(f"RAG DEPLOYMENT finished successfully for Task ID: {task.id}.")
        return deployer_result