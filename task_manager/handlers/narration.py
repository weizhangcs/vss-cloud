import json  # [新增] 用于处理 JSON 读写
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
# from ai_services.narration.narration_generator_v3 import NarrationGeneratorV3
from ai_services.narration.narration_generator_v4 import NarrationGeneratorV3
from .base import BaseTaskHandler
from .registry import HandlerRegistry
# [新增导入] 适配层和异常处理
from core.exceptions import BizException
from core.error_codes import ErrorCode
from ai_services.utils.blueprint_converter import BlueprintConverter, Blueprint  # 假设路径


@HandlerRegistry.register(Task.TaskType.GENERATE_NARRATION)
class NarrationHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting NARRATION GENERATION for Task ID: {task.id}...")

        payload = task.payload
        asset_name = payload.get("asset_name")
        asset_id = payload.get("asset_id")
        output_file_path_str = payload.get("absolute_output_path")
        blueprint_path_str = payload.get("absolute_blueprint_path")  # 这是新版 Blueprint 路径

        if not all([asset_name, asset_id, output_file_path_str, blueprint_path_str]):
            raise ValueError("Payload missing required keys.")

        new_blueprint_path = Path(blueprint_path_str)
        if not new_blueprint_path.is_file():
            raise FileNotFoundError(f"Blueprint file not found at: {new_blueprint_path}")

        # --- [核心利旧适配层] 转换新的 Blueprint 格式为旧版 ---

        # 1. 读取新 Blueprint JSON
        with new_blueprint_path.open('r', encoding='utf-8') as f:
            new_blueprint_json = json.load(f)

        # 2. 校验并解析为新 Pydantic 对象
        try:
            new_blueprint_obj = Blueprint.model_validate(new_blueprint_json)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"New blueprint file validation failed: {e}")

        # 3. 实例化并执行转换
        converter = BlueprintConverter()
        old_blueprint_dict = converter.convert(new_blueprint_obj)

        # 4. 保存转换后的旧版 Blueprint 到临时文件
        converted_filename = f"narrative_blueprint_OLD_CONVERTED_narration.json"
        # 确保写入任务的临时目录
        temp_dir = settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace"
        temp_dir.mkdir(parents=True, exist_ok=True)
        converted_path = temp_dir / converted_filename

        with converted_path.open('w', encoding='utf-8') as f:
            json.dump(old_blueprint_dict, f, ensure_ascii=False, indent=2)

        # 5. 覆盖 Blueprint 路径：让下游服务使用转换后的文件
        final_blueprint_path = converted_path
        # ----------------------------------------------------------

        # 1. 配置 (保持不变)
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            default_config = ai_config_full.get('narration_generator', {})
        except Exception:
            self.logger.warning("Using default AI config.")
            default_config = {}

        user_params = payload.get("service_params", {})
        final_config = default_config.copy()
        final_config.update(user_params)
        if 'lang' not in final_config:
            final_config['lang'] = 'zh'

        # 2. 实例化依赖 (保持不变)
        org_id = str(task.organization.org_id)
        corpus_display_name = f"{asset_id}-{org_id}"

        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"narration_task_{task.id}_debug"
        )

        # [New] 初始化计费器
        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        narration_base = settings.BASE_DIR / 'ai_services' / 'narration'

        # 3. 实例化 V3 Generator
        generator = NarrationGeneratorV3(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            prompts_dir=narration_base / 'prompts',
            metadata_dir=narration_base / 'metadata',
            rag_schema_path=settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json',
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 4. 执行
        result_data = generator.execute(
            asset_name=asset_name,
            corpus_display_name=corpus_display_name,
            blueprint_path=final_blueprint_path,  # <-- [修改] 传入转换后的临时文件路径
            config=final_config,
            asset_id=asset_id
        )

        # 5. 保存 (保持不变)
        output_file_path = Path(output_file_path_str)
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Narration script generated successfully (V3) via Legacy Blueprint Converter.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "usage_report": result_data.get("ai_total_usage", {})
        }