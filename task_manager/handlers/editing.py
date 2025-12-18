import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

# Infrastructure
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from core.exceptions import BizException
from core.error_codes import ErrorCode

# Biz Services
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from ai_services.biz_services.editing.schemas import EditingTaskPayload
from ai_services.biz_services.editing.broll_selector_service import BrollSelectorService


@HandlerRegistry.register(Task.TaskType.GENERATE_EDITING_SCRIPT)
class EditingScriptHandler(BaseTaskHandler):

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting EDITING Task: {task.id}")

        # 1. Payload 校验
        try:
            payload_obj = EditingTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Payload: {e}")

        # 2. 路径准备
        dubbing_path = Path(payload_obj.absolute_input_dubbing_path)
        if not dubbing_path.is_absolute(): dubbing_path = settings.SHARED_ROOT / dubbing_path

        blueprint_path = Path(payload_obj.blueprint_path)
        if not blueprint_path.is_absolute(): blueprint_path = settings.SHARED_ROOT / blueprint_path

        output_path = Path(payload_obj.absolute_output_path)

        debug_dir = settings.SHARED_LOG_ROOT / f"editing_{task.id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # 3. 加载数据
        try:
            with dubbing_path.open(encoding='utf-8') as f:
                dubbing_data = json.load(f)

            with blueprint_path.open(encoding='utf-8') as f:
                dataset_raw = json.load(f)
            dataset_obj = NarrativeDataset(**dataset_raw)
        except Exception as e:
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Failed to load inputs: {e}")

        # 4. 初始化 Service
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=payload_obj.service_params.debug,
            debug_dir=debug_dir
        )

        service = BrollSelectorService(
            prompts_dir=settings.BASE_DIR / 'ai_services' / 'biz_services' / 'editing' / 'prompts',
            localization_path=settings.BASE_DIR / 'ai_services' / 'biz_services' / 'editing' / 'localization' / 'broll_selector_service.json',
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"editing_{task.id}_workspace",
            gemini_processor=gemini_processor
        )

        # 5. 执行
        result_data = service.execute(
            dubbing_data=dubbing_data,
            dataset=dataset_obj,
            config=payload_obj.service_params
        )

        # 6. 保存结果
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        # 7. 返回相对路径
        try:
            rel_output = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output = output_path.name

        return {
            "message": "Editing script generated.",
            "output_file_path": str(rel_output),
            "total_sequences": result_data.get("total_sequences", 0)
        }