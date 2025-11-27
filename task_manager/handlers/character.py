import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
from ai_services.analysis.character.character_identifier import CharacterIdentifier
from .base import BaseTaskHandler
from .registry import HandlerRegistry

@HandlerRegistry.register(Task.TaskType.CHARACTER_IDENTIFIER)
class CharacterIdentifierHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting CHARACTER IDENTIFIER for Task ID: {task.id}...")

        payload = task.payload
        input_file_path_str = payload.get("absolute_input_file_path")
        output_file_path_str = payload.get("absolute_output_path")
        service_params = payload.get("service_params", {})

        if not all([input_file_path_str, output_file_path_str]) or service_params is None:
            raise ValueError("Payload missing required paths or service_params.")

        # 1. 加载配置
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            ai_config = ai_config_full.get('character_identifier', {})
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
        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        service_name = CharacterIdentifier.SERVICE_NAME
        prompts_dir = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'prompts'
        localization_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'localization' / f"{service_name}.json"
        schema_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'metadata' / "fact_attributes.json"

        identifier_service = CharacterIdentifier(
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator,
            prompts_dir=prompts_dir,
            localization_path=localization_path,
            schema_path=schema_path,
            logger=self.logger,
            base_path=settings.SHARED_TMP_ROOT / f"task_{task.id}_workspace"
        )

        # 3. 执行
        input_file_path = Path(input_file_path_str)
        if not input_file_path.is_file():
            raise FileNotFoundError(f"Input file not found: {input_file_path}")

        final_params = ai_config.copy()
        final_params.update(service_params)

        result_data = identifier_service.execute(
            enhanced_script_path=input_file_path,
            **final_params
        )

        if result_data.get("status") != "success":
            raise RuntimeError(f"Service returned failure: {result_data}")

        # 4. 保存结果
        output_file_path = Path(output_file_path_str)
        data_payload = result_data.get("data", {})
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(data_payload.get("result", {}), f, ensure_ascii=False, indent=2)

        return {
            "message": "Character identification completed.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "usage_report": data_payload.get("usage", {})
        }