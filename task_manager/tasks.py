# task_manager/tasks.py
import time
import tempfile
import json
import yaml
from pathlib import Path
from typing import Dict

from celery import shared_task
from celery.utils.log import get_task_logger  # 1. 导入 Celery 的专业日志记录器
from django.utils import timezone
from google.cloud import storage

from utils.gcs_utils import upload_file_to_gcs

from .models import Task
from ai_services.rag.deployer import RagDeployer
from ai_services.rag.schemas import load_i18n_strings
from django.conf import settings

from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
from ai_services.analysis.character.character_identifier import CharacterIdentifier
from ai_services.narration.narration_generator import NarrationGenerator
from ai_services.editing.broll_selector_service import BrollSelectorService

from ai_services.dubbing.dubbing_engine import DubbingEngine
from ai_services.dubbing.strategies.aliyun_paieas_strategy import AliyunPAIEASStrategy # <-- [新增]
from ai_services.dubbing.strategies.base_strategy import TTSStrategy

# 2. 获取一个 Celery Task 专用的 logger 实例
logger = get_task_logger(__name__)


@shared_task
def execute_cloud_native_task(task_id):
    """
    Celery 任务，使用专业的日志记录。
    """
    logger.info(f"Celery worker successfully received task with ID: {task_id}")

    try:
        task = Task.objects.get(pk=task_id)
        # [修改] 我们不再在这里修改状态，让 FSM 来处理
        # task.status = Task.TaskStatus.RUNNING
        # task.save()

        # [修改] 使用 FSM 的 'start' 转换
        # 这要求 'start' 转换的目标是 RUNNING，源是 PENDING
        # （假设 TaskCreateView 创建的任务是 PENDING）
        # 如果 TaskCreateView 创建的是 ASSIGNED，则源应为 ASSIGNED

        # 为了简单起见，我们暂时保持手动状态设置
        #task.status = Task.TaskStatus.RUNNING
        #task.save()

        task.start()
        task.started_at = timezone.now()  # 强制写入
        task.save()

        if task.task_type == Task.TaskType.DEPLOY_RAG_CORPUS:
            result = _handle_rag_deployment(task)
        elif task.task_type == Task.TaskType.GENERATE_NARRATION:
            result = _handle_narration_generation(task)
        elif task.task_type == Task.TaskType.CHARACTER_IDENTIFIER:
            result = _handle_character_identification(task)
        elif task.task_type == Task.TaskType.GENERATE_EDITING_SCRIPT:
            result = _handle_editing_script_generation(task)
        elif task.task_type == Task.TaskType.GENERATE_DUBBING:
            result = _handle_dubbing_generation(task)
        else:
            raise ValueError(f"Unsupported cloud-native task type: {task.task_type}")

        # [修改] 使用 FSM 的 'complete' 转换
        #task.complete(result_data=result)
        #task.save()

        task.complete(result_data=result)
        task.finished_at = timezone.now()  # 强制写入
        task._calculate_duration()  # 显式调用计算
        task.save()

        logger.info(f"Task {task_id} ({task.task_type}) completed successfully.")
        return f"Task {task_id} ({task.task_type}) completed successfully."

    except Exception as e:
        logger.error(f"An error occurred while executing Task ID {task_id}: {e}", exc_info=True)
        try:
            task = Task.objects.get(pk=task_id)
            # [修改] 使用 FSM 的 'fail' 转换
            #task.fail(error_message=str(e))
            #task.save()

            task.fail(error_message=str(e))
            task.finished_at = timezone.now()  # 强制写入
            task._calculate_duration()  # 显式调用计算
            task.save()
        except Task.DoesNotExist:
            pass
        return f"Task {task_id} failed."


def _handle_narration_generation(task: Task) -> dict:
    """
    [V2 重构版] 处理“生成解说词”任务。
    集成 NarrationGeneratorV2 (Query-Enhance-Synthesize 架构)。
    """
    logger.info(f"Starting NARRATION GENERATION (V2) for Task ID: {task.id}...")

    payload = task.payload
    asset_name = payload.get("asset_name")
    # [核心修改] 获取 asset_id 替代 series_id
    asset_id = payload.get("asset_id")
    output_file_path_str = payload.get("absolute_output_path")

    # [新增] V2 必须依赖蓝图文件进行上下文增强
    # 客户端创建任务时，必须在 payload 中提供 'blueprint_path'
    blueprint_path_str = payload.get("absolute_blueprint_path")

    if not all([asset_name, asset_id, output_file_path_str, blueprint_path_str]):
        raise ValueError(
            "Payload for GENERATE_NARRATION is missing required keys: "
            "asset_name, asset_id, absolute_output_path, or absolute_blueprint_path."
        )

    blueprint_path = Path(blueprint_path_str)
    if not blueprint_path.is_file():
        raise FileNotFoundError(f"Blueprint file not found at: {blueprint_path}")

    # --- 1. 准备配置 (Merge Default YAML + User Params) ---
    config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
    try:
        with config_path.open('r', encoding='utf-8') as f:
            ai_config_full = yaml.safe_load(f)
        default_config = ai_config_full.get('narration_generator', {})
    except Exception as e:
        logger.warning(f"Failed to load AI config from YAML: {e}. Using empty defaults.")
        default_config = {}

    # 获取用户传入的参数
    user_params = payload.get("service_params", {})

    # [策略] 深度合并配置
    # 注意：V2 的 config 结构较为复杂（包含 control_params），简单的 update 可能不够
    # 但为了保持简单，我们假设用户传的是完整的 control_params 覆盖
    final_config = default_config.copy()
    final_config.update(user_params)

    # 确保 lang 存在，供 V2 内部使用
    if 'lang' not in final_config:
        final_config['lang'] = 'zh'  # 默认中文

    logger.info(f"Final V2 Config: {json.dumps(final_config, ensure_ascii=False)}")

    # --- 2. 实例化 V2 服务 ---
    # 构建 Corpus Name
    org_id = str(task.organization.org_id)
    corpus_display_name = f"{asset_id}-{org_id}"

    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=settings.SHARED_LOG_ROOT / f"narration_task_{task.id}_debug"
    )

    # 定义 V2 所需的元数据路径
    narration_base = settings.BASE_DIR / 'ai_services' / 'narration'

    generator_v2 = NarrationGenerator(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        prompts_dir=narration_base / 'prompts',
        metadata_dir=narration_base / 'metadata',  # V2 新增
        rag_schema_path=settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json',  # V2 新增
        logger=logger,
        work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
        gemini_processor=gemini_processor
    )

    # --- 3. 执行生成 ---
    result_data = generator_v2.execute(
        asset_name=asset_name,
        corpus_display_name=corpus_display_name,
        blueprint_path=blueprint_path,
        config=final_config,
        asset_id=asset_id
    )

    # --- 4. 保存结果 ---
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"NARRATION GENERATION (V2) finished. Output saved to: {output_file_path}")

    return {
        "message": "Narration script generated successfully (V2 Engine).",
        "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        "usage_report": result_data.get("ai_total_usage", {})
    }


def _handle_rag_deployment(task: Task) -> dict:
    """
    [重构后] 处理“部署RAG语料库”任务。
    """
    logger.info(f"Starting RAG DEPLOYMENT for Task ID: {task.id}...")

    payload = task.payload
    # [修改] 键名现在由 View 自动生成
    blueprint_path_str = payload.get("absolute_blueprint_input_path")
    facts_path_str = payload.get("absolute_facts_input_path")

    if not all([blueprint_path_str, facts_path_str]):
        raise ValueError("Payload for DEPLOY_RAG_CORPUS is missing required absolute paths.")

    local_blueprint_path = Path(blueprint_path_str)
    local_facts_path = Path(facts_path_str)

    # [不变] 临时目录应转到 TMP_ROOT
    temp_dir = settings.SHARED_TMP_ROOT / f"rag_deploy_{task.id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # [核心修改] 使用 Organization ID (UUID) 作为租户隔离标识，而非 Name
    # 这将决定 GCS 中的文件夹名称 (rag-engine-source/{org_id}/...)
    org_id = str(task.organization.org_id)

    # 2. 获取 Asset ID (UUID) - 替代原有的 series_id
    asset_id = payload.get("asset_id")
    if not asset_id:
        raise ValueError("Payload missing required 'asset_id'.")

    # 语料库显示名称建议也包含 ID，或者保持 ID+Name 的组合以增强可读性
    # 但为了底层稳定性，我们主要依赖 ID
    series_id = payload.get("series_id")

    # 3. 构建 Corpus Name: {asset_id}-{org_id}
    corpus_display_name = f"{asset_id}-{org_id}"

    deployer = RagDeployer(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        logger=logger
    )
    i18n_path = settings.BASE_DIR / 'ai_services' / 'rag' / 'metadata' / 'schemas.json'
    load_i18n_strings(i18n_path)

    deployer_result = deployer.execute(
        corpus_display_name=corpus_display_name,
        blueprint_path=local_blueprint_path,
        facts_path=local_facts_path,
        gcs_bucket_name=settings.GCS_DEFAULT_BUCKET,
        staging_dir=temp_dir / "staging",
        org_id=org_id,  # 传入 org_id
        asset_id=asset_id  # 传入 asset_id
    )

    logger.info(f"Backing up source files for Task {task.id} to GCS...")
    backup_prefix = f"archive/tasks/{task.id}/inputs"
    upload_file_to_gcs(
        local_file_path=local_blueprint_path,
        bucket_name=settings.GCS_DEFAULT_BUCKET,
        gcs_object_name=f"{backup_prefix}/narrative_blueprint.json"
    )
    upload_file_to_gcs(
        local_file_path=local_facts_path,
        bucket_name=settings.GCS_DEFAULT_BUCKET,
        gcs_object_name=f"{backup_prefix}/character_facts.json"
    )
    logger.info("Source file backup to GCS completed.")
    logger.info(f"RAG DEPLOYMENT finished successfully for Task ID: {task.id}.")

    return deployer_result


def _handle_character_identification(task: Task) -> dict:
    """
    处理“人物事实识别”任务的核心逻辑。
    """
    logger.info(f"Starting CHARACTER IDENTIFIER for Task ID: {task.id}...")

    payload = task.payload
    # [修改] 键名现在由 View 自动生成
    input_file_path_str = payload.get("absolute_input_file_path")
    output_file_path_str = payload.get("absolute_output_path") # 由 View 注入
    service_params = payload.get("service_params", {})

    # [修改] 允许 service_params 为空字典
    if not all([input_file_path_str, output_file_path_str]) or service_params is None:
        raise ValueError("Payload is missing required absolute paths or service_params.")

    # --- [新增] 1. 加载 AI 推理配置 ---
    # 使用 settings.BASE_DIR 来构建配置文件的绝对路径
    config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
    try:
        with config_path.open('r', encoding='utf-8') as f:
            ai_config_full = yaml.safe_load(f)
        # 提取 character_identifier 块的默认值
        ai_config = ai_config_full.get('character_identifier', {})
    except FileNotFoundError:
        logger.error(f"AI 配置 YAML 文件未找到: {config_path}")
        raise
    except KeyError:
        logger.error(f"AI 配置 YAML 文件中缺少 'character_identifier' 块。")
        raise
    # --- [新增结束] ---

    logger.info("Assembling dependencies for CharacterIdentifier service...")
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        # [修改] 调试日志应转到 LOG_ROOT
        debug_dir=settings.SHARED_LOG_ROOT / f"task_{task.id}_debug"
    )
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    service_name = CharacterIdentifier.SERVICE_NAME
    prompts_dir = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'prompts'
    localization_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'localization' / f"{service_name}.json"
    schema_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'metadata' / "fact_attributes.json"

    identifier_service = CharacterIdentifier(
        gemini_processor=gemini_processor,
        cost_calculator=cost_calculator,
        prompts_dir=prompts_dir,
        localization_path=localization_path,
        schema_path=schema_path,
        logger=logger,
        # [修改] 工作目录应转到 TMP_ROOT
        base_path=settings.SHARED_TMP_ROOT / f"task_{task.id}_workspace"
    )

    input_file_path = Path(input_file_path_str)
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    # --- [新增] 2. 合并配置并调用服务 ---
    # 默认配置 (ai_config) 复制，然后用用户参数 (service_params) 覆盖，确保用户传入的参数优先级最高
    final_params = ai_config.copy()
    final_params.update(service_params)
    logger.info(f"Final AI inference parameters: {final_params}")

    result_data = identifier_service.execute(
        enhanced_script_path=input_file_path,
        **final_params # <-- 传入合并后的参数
    )
    # --- [修改结束] ---

    if result_data.get("status") != "success":
        raise RuntimeError(f"CharacterIdentifier service returned a non-success status: {result_data}")

    data_payload = result_data.get("data", {})
    result_to_save = data_payload.get("result", {})
    usage_report = data_payload.get("usage", {})

    # [不变] View 提供了正确的 'absolute_output_path' (在 TMP_ROOT 中)
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_to_save, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER IDENTIFIER finished. Output saved to: {output_file_path}")

    return {
        "message": "Task completed successfully.",
        # [修改] 返回相对于 SHARED_ROOT 的路径
        "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        "usage_report": usage_report
    }



def _handle_editing_script_generation(task: Task) -> dict:
    """
    处理“生成剪辑脚本”任务的核心逻辑。
    """
    logger.info(f"Starting EDITING SCRIPT GENERATION for Task ID: {task.id}...")

    payload = task.payload
    # [修改] 键名现在由 View 自动生成
    dubbing_path_str = payload.get("absolute_dubbing_script_path")
    blueprint_path_str = payload.get("absolute_blueprint_path")
    output_file_path_str = payload.get("absolute_output_path") # 由 View 注入
    service_params = payload.get("service_params", {})



    if not all([dubbing_path_str, blueprint_path_str, output_file_path_str]):
        raise ValueError("Payload for GENERATE_EDITING_SCRIPT is missing required absolute paths.")

    # --- [新增] 1. 加载 AI 推理配置 ---
    config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'

    # 定义路径
    prompts_dir = settings.BASE_DIR / 'ai_services' / 'editing' / 'prompts'
    # [新增] 定义语言包路径
    localization_path = settings.BASE_DIR / 'ai_services' / 'editing' / 'localization' / 'broll_selector_service.json'

    try:
        with config_path.open('r', encoding='utf-8') as f:
            ai_config_full = yaml.safe_load(f)
        # 提取 broll_selector_service 块的默认值
        ai_config = ai_config_full.get('broll_selector_service', {})
    except FileNotFoundError:
        logger.error(f"AI 配置 YAML 文件未找到: {config_path}")
        raise
    except KeyError:
        logger.error(f"AI 配置 YAML 文件中缺少 'broll_selector_service' 块。")
        raise
    # --- [新增结束] ---

    logger.info("Assembling dependencies for BrollSelectorService...")
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        # [修改] 调试日志应转到 LOG_ROOT
        debug_dir=settings.SHARED_LOG_ROOT / f"task_{task.id}_debug"
    )
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    selector_service = BrollSelectorService(
        prompts_dir=prompts_dir,
        localization_path=localization_path,
        logger=logger,
        # [不变] 工作目录应转到 TMP_ROOT
        work_dir=settings.SHARED_TMP_ROOT / f"editing_task_{task.id}_workspace",
        gemini_processor=gemini_processor
    )

    # --- [修改] 2. 合并配置并调用服务 ---
    # 默认配置 (ai_config) 复制，然后用用户参数 (service_params) 覆盖，确保用户传入的参数优先级最高
    final_params = ai_config.copy()
    final_params.update(service_params)
    logger.info(f"Final AI inference parameters: {final_params}")

    result_data = selector_service.execute(
        dubbing_path=Path(dubbing_path_str),
        blueprint_path=Path(blueprint_path_str),
        **final_params  # <-- 传入合并后的参数
    )

    # [不变] View 提供了正确的 'absolute_output_path' (在 TMP_ROOT 中)
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"EDITING SCRIPT GENERATION finished. Output saved to: {output_file_path}")

    return {
        "message": "Editing script generated successfully.",
        # [修改] 返回相对于 SHARED_ROOT 的路径
        "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        "script_summary": {
            "total_sequences": len(result_data.get("editing_script", []))
        }
    }


def _handle_dubbing_generation(task: Task) -> dict:
    """
    [新增] 处理“生成配音”任务的核心逻辑（组合根）。
    集成 DubbingEngineV2。
    """
    logger.info(f"Starting DUBBING GENERATION for Task ID: {task.id}...")

    payload = task.payload
    narration_path_str = payload.get("absolute_input_narration_path")
    output_file_path_str = payload.get("absolute_output_path")
    service_params = payload.get("service_params", {})

    template_name = service_params.pop("template_name", None)

    if not all([narration_path_str, output_file_path_str, template_name]):
        raise ValueError("Payload for GENERATE_DUBBING is missing required keys.")

    narration_path = Path(narration_path_str)
    output_json_path = Path(output_file_path_str)

    # 2. 为此任务创建专属的音频文件输出目录
    audio_work_dir = settings.SHARED_TMP_ROOT / f"dubbing_task_{task.id}_audio"
    audio_work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Audio files will be saved to: {audio_work_dir}")

    # 3. 组装依赖 (Composition Root)
    logger.info("Assembling dependencies for DubbingEngineV2 service...")

    templates_config_path = settings.BASE_DIR / 'ai_services' / 'dubbing' / 'configs' / 'dubbing_templates.yaml'
    with templates_config_path.open('r', encoding='utf-8') as f:
        all_templates = yaml.safe_load(f)

    # 实例化策略
    strategy_paieas = AliyunPAIEASStrategy(
        service_url=settings.PAI_EAS_SERVICE_URL,
        token=settings.PAI_EAS_TOKEN
    )

    available_strategies: Dict[str, TTSStrategy] = {
        "aliyun_paieas": strategy_paieas,
        # 未来可以在这里添加 "google_tts", "elevenlabs" 等
    }

    # [新增] 定义 metadata 目录 (用于加载 tts_instructs.json)
    metadata_dir = settings.BASE_DIR / 'ai_services' / 'dubbing' / 'metadata'

    # 4. 实例化 DubbingEngineV2
    dubbing_service = DubbingEngine(
        logger=logger,
        work_dir=audio_work_dir,
        strategies=available_strategies,
        templates=all_templates,
        metadata_dir=metadata_dir,  # <-- [V2 新增依赖]
        shared_root_path=settings.SHARED_ROOT
    )

    # 5. 执行服务
    result_data = dubbing_service.execute(
        narration_path=narration_path,
        template_name=template_name,
        **service_params
    )

    # 6. 保存结果
    with output_json_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"DUBBING GENERATION finished. Output saved to: {output_json_path}")

    return {
        "message": "Dubbing script and audio files generated successfully (V2 Engine).",
        "output_file_path": str(output_json_path.relative_to(settings.SHARED_ROOT)),
        "audio_output_directory": str(audio_work_dir.relative_to(settings.SHARED_ROOT)),
        "total_clips_generated": len(result_data.get("dubbing_script", []))
    }