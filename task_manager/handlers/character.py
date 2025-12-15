import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.analysis.character.character_identifier import CharacterIdentifier
from .base import BaseTaskHandler
from .registry import HandlerRegistry

from core.exceptions import BizException
from core.error_codes import ErrorCode

from ai_services.utils.blueprint_converter import BlueprintConverter, Blueprint

@HandlerRegistry.register(Task.TaskType.CHARACTER_IDENTIFIER)
class CharacterIdentifierHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting CHARACTER IDENTIFIER for Task ID: {task.id}...")

        payload = task.payload

        #input_file_path_str = payload.get("absolute_input_file_path")
        # input_file_path_str 现在指向的是新的 Blueprint 文件
        new_blueprint_path_str = payload.get("absolute_input_file_path")

        output_file_path_str = payload.get("absolute_output_path")
        service_params = payload.get("service_params", {})

        # [新增] 从新的 Blueprint Schema 中提取角色列表，如果客户端没有提供的话
        characters_to_analyze = service_params.get("characters_to_analyze")

        if not all([new_blueprint_path_str, output_file_path_str]) or service_params is None:
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

        # 2. 【核心利旧适配层】转换新的 Blueprint 格式为旧版
        new_blueprint_path = Path(new_blueprint_path_str)
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

        # c. [升级点] 如果调用方未提供角色列表，则从新 Blueprint 中提取
        if not characters_to_analyze or len(characters_to_analyze) == 0:
            characters_to_analyze = new_blueprint_obj.global_character_list
            if not characters_to_analyze:
                self.logger.warning(
                    "No characters specified in payload or blueprint. Analyzing all main dialogue speakers.")

        # d. 实例化并执行转换
        converter = BlueprintConverter()
        old_blueprint_dict = converter.convert(new_blueprint_obj)

        # e. 保存转换后的旧版 Blueprint 到临时文件
        # 文件名保持唯一性，并指向临时目录
        converted_filename = f"converted_old_{new_blueprint_path.name}"
        converted_path = settings.SHARED_TMP_ROOT / converted_filename

        with converted_path.open('w', encoding='utf-8') as f:
            json.dump(old_blueprint_dict, f, ensure_ascii=False, indent=2)

        # f. 覆盖输入路径：让下游服务使用转换后的文件
        final_input_path = converted_path

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

        final_params = ai_config.copy()
        final_params.update(service_params)

        # [修改] 注入最新的 characters_to_analyze
        final_params['characters_to_analyze'] = characters_to_analyze

        result_data = identifier_service.execute(
            enhanced_script_path=final_input_path,  # <-- 使用转换后的临时文件
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