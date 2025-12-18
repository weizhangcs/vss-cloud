# ai_services/biz_services/analysis/character/character.py
import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from .registry import HandlerRegistry
from .base import BaseTaskHandler

# 引入数据基座
from ai_services.biz_services.narrative_dataset import NarrativeDataset

# [新增] 引入 Input Schemas
from ai_services.biz_services.analysis.character.schemas import CharacterTaskPayload

# 引入业务服务
from ai_services.biz_services.analysis.character.character_identifier import CharacterIdentifier
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.CHARACTER_IDENTIFIER)
class CharacterHandler(BaseTaskHandler):
    """
    [Handler] 角色分析任务处理器 (Refactored: Fully Schema-Driven)
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting CHARACTER IDENTIFIER for Task ID: {task.id}...")

        # --- [Step 1: 基于 Schema 的输入校验] ---
        # 这一步替代了之前所有的 manual payload parsing
        try:
            # Pydantic 会自动处理类型转换、默认值填充和必填项检查
            payload_obj = CharacterTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Task Payload: {e}")

        # 路径对象化
        input_path = Path(payload_obj.absolute_input_file_path)

        # 输出路径逻辑
        if payload_obj.absolute_output_path:
            output_path = Path(payload_obj.absolute_output_path)
        else:
            output_path = input_path.parent / f"{input_path.stem}_character_analysis.json"

        # 提取业务参数对象
        params = payload_obj.service_params

        # 简单的业务逻辑防御
        if not params.characters_to_analyze:
            self.logger.warning("No 'characters_to_analyze' provided in service_params.")

        # --- [Step 2: 加载并校验 NarrativeDataset] ---
        try:
            with input_path.open('r', encoding='utf-8') as f:
                raw_data = json.load(f)

            # [Strict Mode]
            dataset = NarrativeDataset(**raw_data)
            self.logger.info(f"NarrativeDataset loaded successfully. Scenes: {len(dataset.scenes)}")

        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR,
                               msg=f"Input file is not a valid NarrativeDataset: {e}")

        # --- [Step 3: 初始化基础设施] ---
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"char_task_{task.id}_debug"
        )

        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        # --- [Step 4: 初始化业务服务] ---
        analysis_base = settings.BASE_DIR / 'ai_services' / 'biz_services' / 'analysis' / 'character'

        identifier = CharacterIdentifier(
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator,
            prompts_dir=analysis_base / 'prompts',
            localization_path=analysis_base / 'localization' / 'character_identifier.json',
            schema_path=analysis_base / 'metadata' / 'fact_attributes.json',
            logger=self.logger,
            base_path=settings.SHARED_TMP_ROOT / f"char_task_{task.id}_work"
        )

        # --- [Step 5: 执行] ---
        # 直接使用 Pydantic 对象中的强类型属性
        result_envelope = identifier.execute(
            enhanced_script_path=input_path,
            characters_to_analyze=params.characters_to_analyze,
            lang=params.lang,
            default_model=params.model,
            default_temp=params.temp
        )

        # --- [Step 6: 结果落盘] ---
        output_path.parent.mkdir(parents=True, exist_ok=True)

        final_result = result_envelope.get('data', {}).get('result', {})
        usage_report = result_envelope.get('data', {}).get('usage', {})

        with output_path.open('w', encoding='utf-8') as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)

        return {
            "message": "Character analysis completed successfully.",
            "output_file_path": str(output_path),
            "usage_report": usage_report
        }