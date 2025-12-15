import json
import yaml
from pathlib import Path
from typing import Dict
from django.conf import settings
from task_manager.models import Task
# 注意：引用去除了 V2 后缀的引擎
from ai_services.biz_services.dubbing.dubbing_engine import DubbingEngine
from ai_services.ai_platform.tts.strategies.aliyun_paieas_strategy import AliyunPAIEASStrategy
from ai_services.ai_platform.tts.strategies.google_tts_strategy import GoogleTTSStrategy
from ai_services.ai_platform.tts.strategies.base_strategy import TTSStrategy
from .base import BaseTaskHandler
from .registry import HandlerRegistry


@HandlerRegistry.register(Task.TaskType.GENERATE_DUBBING)
class DubbingHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting DUBBING GENERATION for Task ID: {task.id}...")

        payload = task.payload
        narration_path_str = payload.get("absolute_input_narration_path")
        output_file_path_str = payload.get("absolute_output_path")
        service_params = payload.get("service_params", {})
        template_name = service_params.pop("template_name", None)

        if not all([narration_path_str, output_file_path_str, template_name]):
            raise ValueError("Payload missing required keys.")

        narration_path = Path(narration_path_str)
        output_json_path = Path(output_file_path_str)

        # 1. 创建专属音频输出目录
        audio_work_dir = settings.SHARED_TMP_ROOT / f"dubbing_task_{task.id}_audio"
        audio_work_dir.mkdir(parents=True, exist_ok=True)

        # 2. 加载模板
        templates_config_path = settings.BASE_DIR / 'ai_services' / 'dubbing' / 'configs' / 'dubbing_templates.yaml'
        with templates_config_path.open('r', encoding='utf-8') as f:
            all_templates = yaml.safe_load(f)

        # 3. 初始化策略
        # 这里可以进一步扩展为 Strategy Factory，但暂时保持简单
        strategy_paieas = AliyunPAIEASStrategy(
            service_url=settings.PAI_EAS_SERVICE_URL,
            token=settings.PAI_EAS_TOKEN
        )
        strategy_google = GoogleTTSStrategy()

        available_strategies: Dict[str, TTSStrategy] = {
            "aliyun_paieas": strategy_paieas,
            "google_tts": strategy_google,
        }

        # 4. 初始化引擎
        metadata_dir = settings.BASE_DIR / 'ai_services' / 'dubbing' / 'metadata'

        dubbing_service = DubbingEngine(
            logger=self.logger,
            work_dir=audio_work_dir,
            strategies=available_strategies,
            templates=all_templates,
            metadata_dir=metadata_dir,
            shared_root_path=settings.SHARED_ROOT
        )

        # 5. 执行
        result_data = dubbing_service.execute(
            narration_path=narration_path,
            template_name=template_name,
            **service_params
        )

        # 6. 保存结果
        with output_json_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Dubbing script and audio files generated successfully.",
            "output_file_path": str(output_json_path.relative_to(settings.SHARED_ROOT)),
            "audio_output_directory": str(audio_work_dir.relative_to(settings.SHARED_ROOT)),
            "total_clips_generated": len(result_data.get("dubbing_script", []))
        }