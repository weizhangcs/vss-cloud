# task_manager/handlers/character_pre_annotator.py

import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.character_pre_annotator.service import CharacterPreAnnotatorService
from ai_services.biz_services.character_pre_annotator.schemas import CharacterPreAnnotatorPayload

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.CHARACTER_PRE_ANNOTATOR)
class CharacterPreAnnotatorHandler(BaseTaskHandler):
    """
    [Handler] 角色预处理任务 (V3.7)
    职责：
    1. 接收客户端的相对路径。
    2. 校验文件存在性 (Security Check)。
    3. 调用业务 Service (透传相对路径)。
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting CHARACTER_PRE_ANNOTATOR Task: {task.id}")

        # 1. 基础设施初始化
        debug_dir = settings.SHARED_LOG_ROOT / f"char_pre_{task.id}_debug"
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

        service = CharacterPreAnnotatorService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 2. Payload 校验与路径检查
        try:
            # 2.1 基础格式校验 (Pydantic 拦截绝对路径)
            payload_obj = CharacterPreAnnotatorPayload(**task.payload)

            # 2.2 提取路径进行物理检查
            # 我们直接使用原始 payload，不需要修改它
            service_payload = payload_obj.model_dump()
            raw_path = payload_obj.subtitle_path

            if raw_path.startswith("gs://"):
                self.logger.info(f"Using GCS Path: {raw_path}")
            else:
                # [Core Fix] 仅做存在性检查，不修改 Payload 中的路径
                # 将相对路径锚定到 SHARED_ROOT 进行检查
                absolute_path = settings.SHARED_ROOT / raw_path

                # 二次确认文件存在 (Fail Fast)
                if not absolute_path.exists():
                    raise BizException(ErrorCode.FILE_IO_ERROR, f"Input file not found on server: {absolute_path}")

                self.logger.info(f"Local file verified at: {absolute_path}")
                # 【关键】不要覆盖 service_payload['subtitle_path']
                # 让 Service 接收相对路径，通过 Schema 校验，然后在 Service 内部自行 resolve

        except Exception as e:
            if isinstance(e, BizException): raise e
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 3. 执行业务逻辑
        try:
            result_data = service.execute(service_payload)
        except Exception as e:
            self.logger.error(f"CharacterPreAnnotator execution failed: {e}", exc_info=True)
            raise e

        # 4. 结果落盘
        output_filename = f"character_pre_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"char_pre_{task.id}_workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        # 计算相对路径返回给前端
        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        self.logger.info(f"✅ Task Finished. Result saved to: {rel_output_path}")

        return {
            "message": "Character pre-annotation completed.",
            "output_file_path": str(rel_output_path),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }