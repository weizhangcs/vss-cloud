# task_manager/handlers/localization.py

import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

# Infrastructure
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator

# Biz Services
from ai_services.biz_services.localization.localizer import ContentLocalizer
from ai_services.biz_services.localization.schemas import LocalizationTaskPayload  # [New]
from ai_services.biz_services.narrative_dataset import NarrativeDataset

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.LOCALIZE_NARRATION)
class LocalizationHandler(BaseTaskHandler):

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting Localization Task: {task.id}")

        # --- [Step 1: Schema 校验] ---
        try:
            # 使用 Pydantic 自动校验字段完整性和类型
            payload_obj = LocalizationTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Payload: {e}")

        try:
            # --- [Step 2: 路径准备] ---
            input_script_path = Path(payload_obj.master_script_path)
            if not input_script_path.is_absolute():
                input_script_path = settings.SHARED_ROOT / input_script_path

            if not input_script_path.exists():
                raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Source script not found: {input_script_path}")

            blueprint_path = Path(payload_obj.blueprint_path)
            if not blueprint_path.is_absolute():
                blueprint_path = settings.SHARED_ROOT / blueprint_path

            if not blueprint_path.exists():
                raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Blueprint not found: {blueprint_path}")

            # --- [Step 3: 加载 Dataset] ---
            try:
                with blueprint_path.open('r', encoding='utf-8') as f:
                    raw_data = json.load(f)
                dataset_obj = NarrativeDataset(**raw_data)
            except Exception as e:
                raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid NarrativeDataset: {e}")

            # --- [Step 4: 初始化基础设施] ---
            gemini_processor = GeminiProcessor(
                api_key=settings.GOOGLE_API_KEY,
                logger=self.logger,
                debug_mode=payload_obj.service_params.debug,
                debug_dir=settings.SHARED_LOG_ROOT / "debug_localization" / str(task.id)
            )

            cost_calculator = CostCalculator(
                pricing_data=settings.GEMINI_PRICING,
                usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
            )

            prompts_dir = settings.BASE_DIR / 'ai_services' / 'biz_services' / 'localization' / 'prompts'

            localizer = ContentLocalizer(
                gemini_processor=gemini_processor,
                cost_calculator=cost_calculator,
                prompts_dir=prompts_dir,
                logger=self.logger
            )

            # --- [Step 5: 加载源数据] ---
            with input_script_path.open('r', encoding='utf-8') as f:
                master_script_data = json.load(f)

            # --- [Step 6: 执行业务逻辑] ---
            # 传入 Pydantic 对象 payload_obj.service_params
            result_data = localizer.execute(
                master_script_data=master_script_data,
                config=payload_obj.service_params,  # 传对象
                dataset=dataset_obj
            )

            # --- [Step 7: 结果持久化] ---
            # 使用 Payload 中指定的绝对路径，确保“产出物”可见
            output_path = Path(payload_obj.absolute_output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with output_path.open('w', encoding='utf-8') as f:
                # result_data 已经是 dump 后的 dict
                json.dump(result_data, f, ensure_ascii=False, indent=2)

            return {
                "message": f"Localization to {payload_obj.service_params.target_lang} completed.",
                "output_file_path": str(output_path),
                "usage_report": result_data.get("ai_total_usage", {})
            }

        except Exception as e:
            self.logger.error(f"❌ Localization Task Failed: {str(e)}", exc_info=True)
            raise