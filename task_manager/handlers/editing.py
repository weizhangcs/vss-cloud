import json  # [新增] 用于处理 JSON 读写
import yaml
from pathlib import Path
from django.conf import settings
from task_manager.models import Task
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.biz_services.editing.broll_selector_service import BrollSelectorService
from .base import BaseTaskHandler
from .registry import HandlerRegistry
# [新增导入] 适配层和异常处理
from core.exceptions import BizException
from core.error_codes import ErrorCode
from ai_services.utils.blueprint_converter import BlueprintConverter, Blueprint


@HandlerRegistry.register(Task.TaskType.GENERATE_EDITING_SCRIPT)
class EditingScriptHandler(BaseTaskHandler):
    def handle(self, task: Task) -> dict:
        self.logger.info(f"Starting EDITING SCRIPT GENERATION for Task ID: {task.id}...")

        payload = task.payload
        dubbing_path_str = payload.get("absolute_dubbing_script_path")
        blueprint_path_str = payload.get("absolute_blueprint_path")  # 这是新版 Blueprint 路径
        output_file_path_str = payload.get("absolute_output_path")
        service_params = payload.get("service_params", {})

        if not all([dubbing_path_str, blueprint_path_str, output_file_path_str]):
            raise ValueError("Payload missing required absolute paths.")

        # --- [核心利旧适配层] 转换新的 Blueprint 格式为旧版 ---

        new_blueprint_path = Path(blueprint_path_str)
        if not new_blueprint_path.is_file():
            raise FileNotFoundError(f"Blueprint file not found at: {new_blueprint_path}")

        # 1. 读取新 Blueprint JSON
        with new_blueprint_path.open('r', encoding='utf-8') as f:
            new_blueprint_json = json.load(f)

        # 2. 校验并解析为新 Pydantic 对象
        try:
            new_blueprint_obj = Blueprint.model_validate(new_blueprint_json)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, msg=f"New blueprint file validation failed: {e}")

        # 3. 实例化并执行转换
        converter = BlueprintConverter()
        old_blueprint_dict = converter.convert(new_blueprint_obj)

        # [关键新增] 获取 ID 映射表
        scene_chapter_map = converter.get_scene_chapter_map()

        # 4. 保存转换后的旧版 Blueprint 到临时文件
        converted_filename = f"narrative_blueprint_OLD_CONVERTED_editing.json"
        # 写入任务的临时目录
        temp_dir = settings.SHARED_TMP_ROOT / f"editing_task_{task.id}_workspace"
        temp_dir.mkdir(parents=True, exist_ok=True)
        converted_path = temp_dir / converted_filename

        with converted_path.open('w', encoding='utf-8') as f:
            json.dump(old_blueprint_dict, f, ensure_ascii=False, indent=2)

        # 5. 最终 Blueprint 路径：让下游服务使用转换后的文件
        final_blueprint_path = converted_path
        # --------------------------------------------------------

        # 1. 加载配置 (保持不变)
        config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
        try:
            with config_path.open('r', encoding='utf-8') as f:
                ai_config_full = yaml.safe_load(f)
            ai_config = ai_config_full.get('broll_selector_service', {})
        except Exception:
            self.logger.error("Missing or invalid AI config file.")
            raise

        # 2. 准备依赖 (保持不变)
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=settings.DEBUG,
            debug_dir=settings.SHARED_LOG_ROOT / f"task_{task.id}_debug"
        )

        prompts_dir = settings.BASE_DIR / 'ai_services' / 'editing' / 'prompts'
        localization_path = settings.BASE_DIR / 'ai_services' / 'editing' / 'localization' / 'broll_selector_service.json'

        selector_service = BrollSelectorService(
            prompts_dir=prompts_dir,
            localization_path=localization_path,
            logger=self.logger,
            work_dir=settings.SHARED_TMP_ROOT / f"editing_task_{task.id}_workspace",
            gemini_processor=gemini_processor
        )

        # 3. 执行
        final_params = ai_config.copy()
        final_params.update(service_params)

        result_data = selector_service.execute(
            dubbing_path=Path(dubbing_path_str),
            blueprint_path=final_blueprint_path,  # <-- [修改] 传入转换后的临时文件路径
            **final_params
        )

        # 4. 【核心后处理】替换剪辑脚本中的 Scene ID 为 Chapter UUID
        editing_script = result_data.get("editing_script", [])

        for entry in editing_script:
            for clip in entry.get("b_roll_clips", []):
                # 读取临时的 Scene ID (它在剪辑脚本中是字符串)
                temp_scene_id = str(clip.get("scene_id"))

                if temp_scene_id in scene_chapter_map:
                    # [关键替换] 注入 Chapter UUID (Edge侧消费的核心字段)
                    clip["chapter_id"] = scene_chapter_map[temp_scene_id]
                else:
                    # 记录警告，如果某个 clip 的 scene_id 无法追溯到原始 Chapter，理论上不该发生
                    self.logger.warning(
                        f"Clip referencing unknown Scene ID: {temp_scene_id}. Skipping chapter_id injection.")

        # 5. 保存最终结果
        output_file_path = Path(output_file_path_str)
        # result_data 现在包含了修改后的 editing_script
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        return {
            "message": "Editing script generated successfully via Legacy Blueprint Converter (Chapter ID injected).",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
            "script_summary": {
                "total_sequences": len(editing_script)
            }
        }