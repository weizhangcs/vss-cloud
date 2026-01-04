import json
from pathlib import Path

from django.conf import settings

from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.slice_grouper.service import SliceGrouperService
from ai_services.biz_services.scene_pre_annotator.schemas import ScenePreAnnotatorPayload

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.SLICE_GROUPER)
class SliceGrouperHandler(BaseTaskHandler):
    """
    [Handler] 切片聚类任务 (Atomic Capability)
    职责：
    1. 接收已标注的切片 (Annotated Slices)。
    2. 调用 SliceGrouperService 执行语义聚类。
    3. 将场景结果 (Scenes) 落盘。
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting SLICE_GROUPER Task: {task.id}")

        # 1. 基础设施
        debug_dir = settings.SHARED_LOG_ROOT / f"slice_grouper_{task.id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=True,
            debug_dir=debug_dir
        )

        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        service = SliceGrouperService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 2. Payload 准备
        try:
            # 简单校验 Schema
            ScenePreAnnotatorPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 3. 执行
        try:
            result_data = service.execute(task.payload)
        except Exception as e:
            self.logger.error(f"SliceGrouperService execution failed: {e}", exc_info=True)
            raise e

        # 4. 结果落盘
        output_filename = f"slice_grouper_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"slice_grouper_{task.id}_workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        return {
            "message": "Slice grouping completed.",
            "output_file_path": str(rel_output_path),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }