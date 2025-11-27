import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.common.gemini.gemini_processor import GeminiProcessor
# 注意：这里引用的是重命名后的 V2 版本（去除了 _v2 后缀）
from ai_services.narration.narration_generator import NarrationGenerator
from .base import BaseTaskHandler
from .registry import HandlerRegistry


@HandlerRegistry.register(Task.TaskType.GENERATE_NARRATION)
class NarrationHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting NARRATION GENERATION for Task ID: {task.id}...")

        payload = task.payload
        asset_name = payload.get("asset_name")
        asset_id = payload.get("asset_id")
        output_file_path_str = payload.get("absolute_output_path")
        blueprint_path_str = payload.get("absolute_blueprint_path")

        if not all([asset_name, asset_id, output_file_path_str, blueprint_path_str]):
            raise ValueError("Payload missing required keys: asset_name, asset_id, output_path, or blueprint_path.")

        blueprint_path = Path(blueprint_path_str)
        if not blueprint_path.is_file():
            raise FileNotFoundError(f"Blueprint file not found at: {blueprint_path}")

        # 1. 加载与合并配置
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            default_config = ai_config_full.get('narration_generator', {})
        except Exception as e:
            self.logger.warning(f"Failed to load AI config: {e}. Using defaults.")
            default_config = {}

        user_params = payload.get("service_params", {})
        final_config = default_config.copy()
        final_config.update(user_params)
        if 'lang' not in final_config:
            final_config['lang'] = 'zh'

        # 2. 实例化服务
        org_id = str(task.organization.org_id)
        corpus_display_name = f"{asset_id}-{org_id}"

        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"narration_task_{task.id}_debug"
        )

        narration_base = settings.BASE_DIR / 'ai_services' / 'narration'

        generator = NarrationGenerator(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            prompts_dir=narration_base / 'prompts',
            metadata_dir=narration_base / 'metadata',
            rag_schema_path=settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json',
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
            gemini_processor=gemini_processor
        )

        # 3. 执行
        result_data = generator.execute(
            asset_name=asset_name,
            corpus_display_name=corpus_display_name,
            blueprint_path=blueprint_path,
            config=final_config,
            asset_id=asset_id
        )

        # 4. 保存结果
        output_file_path = Path(output_file_path_str)
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Narration script generated successfully.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "usage_report": result_data.get("ai_total_usage", {})
        }