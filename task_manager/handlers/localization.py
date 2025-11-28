import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
# 复用 V3 生成器
from ai_services.narration.narration_generator_v4 import NarrationGeneratorV3
from .base import BaseTaskHandler
from .registry import HandlerRegistry


@HandlerRegistry.register(Task.TaskType.LOCALIZE_NARRATION)
class LocalizationHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting LOCALIZATION for Task ID: {task.id}...")

        payload = task.payload
        # 1. 输入：母本 JSON 路径
        master_script_path_str = payload.get("absolute_master_script_path")
        # 2. 输入：蓝图路径 (用于时长校验)
        blueprint_path_str = payload.get("absolute_blueprint_path")
        # 3. 输出路径
        output_file_path_str = payload.get("absolute_output_path")

        if not all([master_script_path_str, blueprint_path_str, output_file_path_str]):
            raise ValueError("Missing required paths (master_script, blueprint, or output).")

        # 加载母本
        master_path = Path(master_script_path_str)
        with master_path.open('r', encoding='utf-8') as f:
            master_data = json.load(f)

        # 准备配置
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            default_config = ai_config_full.get('narration_generator', {})
        except Exception:
            default_config = {}

        # 用户参数
        user_params = payload.get("service_params", {})

        # [关键] 强制检查 target_lang
        if not user_params.get("target_lang"):
            raise ValueError("Localization task requires 'target_lang' parameter.")

        final_config = default_config.copy()
        final_config.update(user_params)
        # 注入 blueprint_path 到 config 字典，供 execute_localization 使用
        final_config['blueprint_path'] = blueprint_path_str

        # 实例化依赖 (同 Narration)
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"localize_task_{task.id}_debug"
        )
        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )
        narration_base = settings.BASE_DIR / 'ai_services' / 'narration'

        # 实例化 Generator
        generator = NarrationGeneratorV3(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            prompts_dir=narration_base / 'prompts',
            metadata_dir=narration_base / 'metadata',
            rag_schema_path=settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json',
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"localize_task_{task.id}_workspace",
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 执行本地化
        result_data = generator.execute_localization(
            master_script_data=master_data,
            config=final_config
        )

        # 保存结果
        output_file_path = Path(output_file_path_str)
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": f"Localization to {final_config.get('target_lang')} completed.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "usage_report": result_data.get("ai_total_usage", {})
        }