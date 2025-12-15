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
# [新增导入] 适配层和异常处理
from core.exceptions import BizException
from core.error_codes import ErrorCode
from ai_services.utils.blueprint_converter import BlueprintConverter, Blueprint


@HandlerRegistry.register(Task.TaskType.LOCALIZE_NARRATION)
class LocalizationHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting LOCALIZATION for Task ID: {task.id}...")

        payload = task.payload
        master_script_path_str = payload.get("absolute_master_script_path")
        blueprint_path_str = payload.get("absolute_blueprint_path")  # 这是新的 Blueprint 路径
        output_file_path_str = payload.get("absolute_output_path")

        if not all([master_script_path_str, blueprint_path_str, output_file_path_str]):
            raise ValueError("Missing required paths (master_script, blueprint, or output).")

        # 1. 加载母本 (保持不变)
        master_path = Path(master_script_path_str)
        with master_path.open('r', encoding='utf-8') as f:
            master_data = json.load(f)

        # 2. --- [核心利旧适配层] 转换新的 Blueprint 格式为旧版 ---
        new_blueprint_path = Path(blueprint_path_str)
        if not new_blueprint_path.is_file():
            raise FileNotFoundError(f"Blueprint file not found at: {new_blueprint_path}")

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
        converted_filename = f"narrative_blueprint_OLD_CONVERTED_localization.json"
        # 写入任务的临时目录
        temp_dir = settings.SHARED_TMP_ROOT / f"localize_task_{task.id}_workspace"
        temp_dir.mkdir(parents=True, exist_ok=True)
        converted_path = temp_dir / converted_filename

        with converted_path.open('w', encoding='utf-8') as f:
            json.dump(old_blueprint_dict, f, ensure_ascii=False, indent=2)

        # e. 最终 Blueprint 路径：让下游服务使用转换后的文件
        final_blueprint_path = converted_path
        # ----------------------------------------------------------------------

        # 3. 准备配置
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

        # [修改] 注入转换后的 Blueprint 路径到 config 字典
        final_config['blueprint_path'] = str(final_blueprint_path)

        # 4. 实例化依赖 (保持不变)
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

        # 5. 实例化 Generator
        generator = NarrationGeneratorV3(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            prompts_dir=narration_base / 'prompts',
            metadata_dir=narration_base / 'metadata',
            rag_schema_path=settings.BASE_DIR / 'ai_services' / 'ai_platform' / 'rag' / 'metadata' / 'schemas.json',
            logger=self.logger,
            work_dir=temp_dir,  # 使用任务的临时目录
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 6. 执行本地化
        result_data = generator.execute_localization(
            master_script_data=master_data,
            config=final_config
        )

        # 7. 保存结果 (保持不变)
        output_file_path = Path(output_file_path_str)
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": f"Localization to {final_config.get('target_lang')} completed (via Legacy Blueprint Converter).",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "usage_report": result_data.get("ai_total_usage", {})
        }