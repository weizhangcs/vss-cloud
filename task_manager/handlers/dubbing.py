import json
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

# Infrastructure
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.tts.strategies.google_tts_strategy import GoogleTTSStrategy
from ai_services.ai_platform.tts.strategies.aliyun_paieas_strategy import AliyunPAIEASStrategy

# Biz Services
from ai_services.biz_services.dubbing.dubbing_engine import DubbingEngine
from ai_services.biz_services.dubbing.schemas import DubbingTaskPayload  # [New Schema]
from ai_services.biz_services.narrative_dataset import NarrativeDataset

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.GENERATE_DUBBING)
class DubbingHandler(BaseTaskHandler):

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting DUBBING Task: {task.id}")

        # 1. Payload 校验
        try:
            payload_obj = DubbingTaskPayload(**task.payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"Invalid Payload: {e}")

        # 2. 路径准备
        # 2.1 输入 Narration 文件
        narration_path = Path(payload_obj.absolute_input_narration_path)
        if not narration_path.is_absolute():
            narration_path = settings.SHARED_ROOT / narration_path
        if not narration_path.exists():
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Input narration not found: {narration_path}")

        # 2.2 输入 Dataset 蓝图 (用于 Context)
        blueprint_path = Path(payload_obj.blueprint_path)
        if not blueprint_path.is_absolute():
            blueprint_path = settings.SHARED_ROOT / blueprint_path

        # 2.3 输出音频目录 (Task ID 隔离)
        audio_work_dir = settings.SHARED_TMP_ROOT / f"dubbing_{task.id}_audio"
        audio_work_dir.mkdir(parents=True, exist_ok=True)

        # 2.4 Debug 目录
        debug_dir = settings.SHARED_LOG_ROOT / f"dubbing_{task.id}_debug"

        # 3. 加载数据
        try:
            with narration_path.open('r', encoding='utf-8') as f:
                narration_data = json.load(f)

            with blueprint_path.open('r', encoding='utf-8') as f:
                dataset_raw = json.load(f)
            dataset_obj = NarrativeDataset(**dataset_raw)
        except Exception as e:
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Failed to load input data: {e}")

        # 4. 初始化引擎
        # 4.1 策略池
        strategies = {
            "google_tts": GoogleTTSStrategy(),
            "aliyun_paieas": AliyunPAIEASStrategy(
                service_url=settings.PAI_EAS_SERVICE_URL,
                token=settings.PAI_EAS_TOKEN
            )
        }

        # 4.2 Gemini (for Director)
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=payload_obj.service_params.debug,
            debug_dir=debug_dir
        )

        # 4.3 Engine
        engine = DubbingEngine(
            logger=self.logger,
            gemini_processor=gemini_processor,
            work_dir=audio_work_dir,
            strategies=strategies,
            templates_config_path=settings.BASE_DIR / 'ai_services' / 'biz_services' / 'dubbing' / 'configs' / 'dubbing_templates.yaml',
            director_prompts_dir=settings.BASE_DIR / 'ai_services' / 'biz_services' / 'dubbing' / 'prompts',  # 注意这里的路径
            shared_root_path=settings.SHARED_ROOT
        )

        # 5. 执行
        result_data = engine.execute(
            narration_data=narration_data,
            dataset=dataset_obj,
            config=payload_obj.service_params
        )

        # 6. 保存结果 JSON
        output_json_path = Path(payload_obj.absolute_output_path)
        output_json_path.parent.mkdir(parents=True, exist_ok=True)

        with output_json_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        # 7. 返回 (相对路径)
        try:
            rel_json_path = output_json_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_json_path = output_json_path.name

        try:
            rel_audio_dir = audio_work_dir.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_audio_dir = audio_work_dir.name

        return {
            "message": "Dubbing completed.",
            "output_file_path": str(rel_json_path),
            "audio_output_dir": str(rel_audio_dir),
            "total_clips": len(result_data.get("dubbing_script", []))
        }