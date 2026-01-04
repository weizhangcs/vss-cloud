import json
from pathlib import Path

from django.conf import settings

from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.visual_analyzer.service import VisualAnalyzerService
from ai_services.biz_services.visual_analyzer.schemas import VisualAnalyzerPayload

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.VISUAL_ANALYZER)
class VisualAnalyzerHandler(BaseTaskHandler):
    """
    [Handler] 视觉分析任务 (Atomic Capability)
    职责：
    1. 调用 VisualAnalyzerService 执行 VLM 标注。
    2. 将标注结果 (Annotated Slices) 落盘。
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting VISUAL_ANALYZER Task: {task.id}")

        # 1. 基础设施
        debug_dir = settings.SHARED_LOG_ROOT / f"visual_analyzer_{task.id}_debug"
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

        service = VisualAnalyzerService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 2. Payload 准备
        # Service 内部处理了 slices_file_path 的加载，这里直接传递 Payload
        try:
            # 简单校验 Schema
            VisualAnalyzerPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 3. 执行
        try:
            result_data = service.execute(task.payload)
        except Exception as e:
            self.logger.error(f"VisualAnalyzerService execution failed: {e}", exc_info=True)
            raise e

        # 4. 结果落盘
        output_filename = f"visual_analyzer_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"visual_analyzer_{task.id}_workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        return {
            "message": "Visual analysis completed.",
            "output_file_path": str(rel_output_path),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }