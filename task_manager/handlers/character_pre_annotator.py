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
    [Handler] 角色预处理任务 (V4.0 JSON-Native)
    职责：
    1. 环境初始化。
    2. 执行业务逻辑并获取结构化结果。
    3. 负责将增量结果 JSON 持久化到磁盘。
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

        # 2. 校验 Payload
        try:
            # 此时 task.payload['subtitle_path'] 已经是符合契约的 JSON 路径
            payload_obj = CharacterPreAnnotatorPayload(**task.payload)
            service_payload = payload_obj.model_dump()
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 3. 执行业务逻辑 (获取 Dict 形式的 CharacterPreAnnotatorResult)
        try:
            raw_result = service.execute(service_payload)
        except Exception as e:
            self.logger.error(f"Service execution failed: {e}", exc_info=True)
            raise e

        # 4. 物理落盘结果 JSON (增量数据)
        # 从 payload 获取预定义的输出路径，或者自动生成
        output_path_str = task.payload.get('absolute_output_path')
        if not output_path_str:
            output_filename = f"character_pre_result_{task.id}.json"
            output_dir = settings.SHARED_TMP_ROOT / f"char_pre_{task.id}_workspace"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / output_filename
        else:
            output_path = Path(output_path_str)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入物理文件
        with open(output_path, 'w', encoding='utf-8') as f:
            # 仅保存 optimized_subtitles 的增量部分
            json.dump(raw_result.get("optimized_subtitles", []), f, ensure_ascii=False, indent=2)

        # 5. 计算相对路径供 API 返回
        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        self.logger.info(f"✅ Task Finished. Result JSON saved to: {rel_output_path}")

        # 6. 返回给 Task Manager 的 result 字段
        # 注意：这里我们只把统计和路径放进数据库，避免把万行级的 optimized_subtitles 塞进数据库 JSONField
        return {
            "message": "Character pre-annotation completed.",
            "output_file_path": str(rel_output_path),
            "stats": raw_result.get("stats"),
            "cost_usage": raw_result.get("usage_report")
        }