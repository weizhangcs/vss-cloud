import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

# 引入核心组件
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.visual_analysis.service import VisualAnalysisService
from ai_services.biz_services.visual_analysis.schemas import VisualAnalysisPayload

# 引入异常处理
from core.exceptions import BizException
from core.error_codes import ErrorCode


# ⚠️ 注意：请确保在 Task.TaskType 枚举中添加了 'VISUAL_ANALYSIS'
# 如果还没有，请去 task_manager/models.py 添加，或者暂时用一个现有的 Type 测试
@HandlerRegistry.register(Task.TaskType.VISUAL_ANALYSIS)
class VisualAnalysisHandler(BaseTaskHandler):

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting VISUAL_ANALYSIS Task: {task.id}")

        # 1. 准备 Debug 目录 (用于存放 Gemini 交互日志)
        debug_dir = settings.SHARED_LOG_ROOT / f"visual_analysis_{task.id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # 2. 初始化基础设施 (Infra)
        # Gemini 处理器
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=True,  # 默认开启调试，方便排查
            debug_dir=debug_dir
        )

        # 成本计算器 (从 settings 加载定价)
        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        # 3. 初始化业务服务 (Service)
        service = VisualAnalysisService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 4. 解析 Payload (确保输入符合 Schema)
        try:
            # 这里允许 payload 只有部分字段，缺省字段由 Schema 默认值填充
            # 但关键路径必须有
            payload_data = task.payload

            # [路径修正] 如果前端传的是相对路径，Service 内部会处理
            # 但为了保险，我们在这里打印一下
            self.logger.info(f"Payload received: {json.dumps(payload_data, ensure_ascii=False)}")

        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload: {e}")

        # 5. 执行核心逻辑
        try:
            # Service.execute 返回的是字典 (result.model_dump())
            result_data = service.execute(payload_data)
        except Exception as e:
            # 捕获已知业务异常或未知异常
            self.logger.error(f"VisualAnalysisService execution failed: {e}", exc_info=True)
            raise e

        # 6. 结果落地 (Save Output)
        # 我们不仅返回给 API，还要把最终的 Timeline JSON 保存到文件，方便下载
        output_filename = f"visual_analysis_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"visual_analysis_{task.id}_workspace"
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

        # 7. 构造 API 响应
        return {
            "message": "Visual analysis and semantic refinement completed.",
            "output_file_path": str(rel_output_path),
            "video_path": result_data.get("video_path"),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }