import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.editing.broll_selector_service import BrollSelectorService
from .base import BaseTaskHandler
from .registry import HandlerRegistry

@HandlerRegistry.register(Task.TaskType.GENERATE_EDITING_SCRIPT)
class EditingScriptHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting EDITING SCRIPT GENERATION for Task ID: {task.id}...")

        payload = task.payload
        dubbing_path_str = payload.get("absolute_dubbing_script_path")
        blueprint_path_str = payload.get("absolute_blueprint_path")
        output_file_path_str = payload.get("absolute_output_path")
        service_params = payload.get("service_params", {})

        if not all([dubbing_path_str, blueprint_path_str, output_file_path_str]):
            raise ValueError("Payload missing required absolute paths.")

        # 1. 加载配置
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            ai_config = ai_config_full.get('broll_selector_service', {})
        except Exception:
            self.logger.error("Missing or invalid AI config file.")
            raise

        # 2. 准备依赖
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"task_{task.id}_debug"
        )

        prompts_dir = settings.BASE_DIR / 'ai_services' / 'editing' / 'prompts'
        localization_path = settings.BASE_DIR / 'ai_services' / 'editing' / 'localization' / 'broll_selector_service.json'

        selector_service = BrollSelectorService(
            prompts_dir=prompts_dir,
            localization_path=localization_path,
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"editing_task_{task.id}_workspace",
            gemini_processor=gemini_processor
        )

        # 3. 执行
        final_params = ai_config.copy()
        final_params.update(service_params)

        result_data = selector_service.execute(
            dubbing_path=Path(dubbing_path_str),
            blueprint_path=Path(blueprint_path_str),
            **final_params
        )

        # 4. 保存结果
        output_file_path = Path(output_file_path_str)
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Editing script generated successfully.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "script_summary": {
                "total_sequences": len(result_data.get("editing_script", []))
            }
        }