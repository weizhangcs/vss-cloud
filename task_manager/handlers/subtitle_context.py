import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

# 引入核心组件
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.character_pre_annotator.service import CharacterPreAnnotatorService
from ai_services.biz_services.character_pre_annotator.schemas import CharacterPreAnnotatorPayload

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.SUBTITLE_CONTEXT)
class SubtitleContextHandler(BaseTaskHandler):

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting SUBTITLE_CONTEXT Task: {task.id}")

        # 1. 准备 Debug 目录
        debug_dir = settings.SHARED_LOG_ROOT / f"subtitle_context_{task.id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # 2. 初始化基础设施
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

        # 3. 初始化业务服务
        service = CharacterPreAnnotatorService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 4. 解析 Payload
        try:
            payload_data = task.payload
            self.logger.info(f"Payload received: {json.dumps(payload_data, ensure_ascii=False)}")
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 5. 执行核心逻辑
        try:
            result_data = service.execute(payload_data)
        except Exception as e:
            self.logger.error(f"CharacterPreAnnotatorService execution failed: {e}", exc_info=True)
            raise e

        # 6. 结果落地
        output_filename = f"subtitle_context_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"subtitle_context_{task.id}_workspace"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / output_filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        # 计算相对路径
        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        self.logger.info(f"✅ Task Finished. Result saved to: {rel_output_path}")

        # 7. 构造 API 响应
        return {
            "message": "Subtitle context refinement completed.",
            "output_file_path": str(rel_output_path),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }