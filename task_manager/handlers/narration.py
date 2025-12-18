import json
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task

# AI Infrastructure
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator

# Biz Services (New Architecture)
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from ai_services.biz_services.narration.narration_generator import NarrationGenerator
from ai_services.biz_services.narration.schemas import NarrationTaskPayload, NarrationServiceConfig

# Base & Registry
from .base import BaseTaskHandler
from .registry import HandlerRegistry

# Exceptions & Utils
from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.GENERATE_NARRATION)
class NarrationHandler(BaseTaskHandler):
    """
    [Orchestrator] Narration 任务处理器 (V5 Schema-Driven & Dataset-Native)

    职责：
    1. Input Validation: 校验路径合法性。
    2. Data Loading: 加载并校验 NarrativeDataset (Strict Mode)。
    3. Context Assembly: 组装 NarrationServiceConfig。
    4. Execution: 驱动 Generator 执行。
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting NARRATION GENERATION for Task ID: {task.id}...")

        # --- [Step 1: 严谨的输入校验 (Input Schema)] ---
        try:
            payload_obj = NarrationTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Task Payload: {e}")

        # --- [Step 2: 数据基座加载 (Dataset Loading)] ---
        # 直接加载 NarrativeDataset，废弃旧的 BlueprintConverter
        try:
            blueprint_path = Path(payload_obj.absolute_blueprint_path)
            with blueprint_path.open(encoding='utf-8') as f:
                raw_data = json.load(f)

            # [Strict Mode Check]
            # 强校验：输入文件必须完全符合 NarrativeDataset 标准
            # 这一步会触发 Pydantic 校验，如果数据不合法（如缺字段），直接抛出异常
            dataset_obj = NarrativeDataset(**raw_data)
            self.logger.info(f"NarrativeDataset loaded successfully. Scenes: {len(dataset_obj.scenes)}")

        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR,
                               msg=f"Input file does not conform to NarrativeDataset standard: {e}")

        # --- [Step 3: 配置加载与合并] ---
        # 3.1 加载系统默认配置
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            system_defaults = ai_config_full.get('narration_generator', {})
        except Exception:
            self.logger.warning("Using default AI config.")
            system_defaults = {}

        # 3.2 准备用户覆盖参数
        user_overrides = payload_obj.service_params

        # --- [Step 4: 构建 Context Carrier (核心上下文对象)] ---
        try:
            # 优先级: System Defaults < User Params < Runtime Constraints
            merged_config_dict = system_defaults.copy()
            merged_config_dict.update(user_overrides)

            # 注入运行时数据
            # [Core Fix] 使用 narrative_dataset 替代旧的 blueprint_data
            merged_config_dict.update({
                "asset_name": payload_obj.asset_name,
                "asset_id": payload_obj.asset_id,

                # [核心修正] 注入 Dataset 对象 (或其 dict)
                # 建议注入 dump 后的 dict，以便 NarrationServiceConfig 重新校验（虽然有些冗余，但解耦更好）
                # 或者直接注入 dataset_obj，因为我们在 schemas.py 里定义的是 Optional[NarrativeDataset]
                "narrative_dataset": dataset_obj,

                "lang": merged_config_dict.get("lang", "zh")
            })

            # 实例化 Config 对象，进行最终的参数校验 (如 ControlParams)
            service_config = NarrationServiceConfig(**merged_config_dict)

        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Service Configuration assembly failed: {e}")

        # --- [Step 5: 实例化基础设施] ---
        org_id = str(task.organization.org_id)
        # RAG Corpus Display Name 通常格式为 "asset_id-org_id"
        corpus_display_name = f"{payload_obj.asset_id}-{org_id}"

        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"narration_task_{task.id}_debug"
        )

        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        narration_base = settings.BASE_DIR / 'ai_services' / 'biz_services' / 'narration'

        generator = NarrationGenerator(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            prompts_dir=narration_base / 'prompts',
            metadata_dir=narration_base / 'metadata',
            rag_schema_path=settings.BASE_DIR / 'ai_services' / 'ai_platform' / 'rag' / 'metadata' / 'schemas.json',
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # --- [Step 6: 执行] ---
        try:
            # [Fix - 关键修改]
            # 问题：直接调用 service_config.model_dump() 会输出 local_id 和 computed fields (duration)，
            # 导致 Generator 内部再次校验 NarrativeDataset 时，因 extra='forbid' 和 alias 不匹配而失败。

            # 解决：
            # 1. Dump 配置时，显式排除 narrative_dataset
            config_payload = service_config.model_dump(mode='json', exclude={'narrative_dataset'})

            # 2. 手动注入原始的 raw_data
            # 这确保了传给 Generator 的是标准的 Input JSON 格式 (带 "id", 不带 "duration")
            config_payload['narrative_dataset'] = raw_data

            result_data = generator.execute(
                asset_name=service_config.asset_name,
                corpus_display_name=corpus_display_name,
                config=config_payload  # 使用修正后的 payload
            )
        except Exception as e:
            raise BizException(ErrorCode.LLM_INFERENCE_ERROR, msg=f"Generator execution failed: {e}")

        # --- [Step 7: 结果持久化] ---
        output_file_path = Path(payload_obj.absolute_output_path)
        # 确保父目录存在
        output_file_path.parent.mkdir(parents=True, exist_ok=True)

        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Narration script generated successfully (V5 Dataset-Native).",
            "output_file_path": str(output_file_path),
            "usage_report": result_data.get("ai_total_usage", {})
        }