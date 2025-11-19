# task_manager/tasks.py
import time
import tempfile
import json
from pathlib import Path
from typing import Dict

from celery import shared_task
from celery.utils.log import get_task_logger  # 1. 导入 Celery 的专业日志记录器
from google.cloud import storage

from utils.gcs_utils import upload_file_to_gcs

from .models import Task
from ai_services.rag.deployer import RagDeployer
from ai_services.rag.schemas import load_i18n_strings
from django.conf import settings

from ai_services.analysis.character.character_identifier import CharacterIdentifier
from ai_services.analysis.character.character_metrics_calculator import CharacterMetricsCalculator
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
from ai_services.narration.narration_generator import NarrationGenerator
from ai_services.editing.broll_selector_service import BrollSelectorService

import yaml # <-- [新增]
from ai_services.dubbing.dubbing_engine import DubbingEngine # <-- [新增]
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
        task.status = Task.TaskStatus.RUNNING
        task.save()

        if task.task_type == Task.TaskType.DEPLOY_RAG_CORPUS:
            result = _handle_rag_deployment(task)
        elif task.task_type == Task.TaskType.GENERATE_NARRATION:
            result = _handle_narration_generation(task)
        elif task.task_type == Task.TaskType.CHARACTER_METRICS:
            result = _handle_character_metrics_calculation(task)
        elif task.task_type == Task.TaskType.CHARACTER_IDENTIFIER:
            result = _handle_character_identification(task)
        elif task.task_type == Task.TaskType.CHARACTER_PIPELINE:
            result = _handle_character_pipeline(task)
        elif task.task_type == Task.TaskType.GENERATE_EDITING_SCRIPT:
            result = _handle_editing_script_generation(task)
        elif task.task_type == Task.TaskType.GENERATE_DUBBING:
            result = _handle_dubbing_generation(task)
        else:
            raise ValueError(f"Unsupported cloud-native task type: {task.task_type}")

        # [修改] 使用 FSM 的 'complete' 转换
        task.complete(result_data=result)
        task.save()

        logger.info(f"Task {task_id} ({task.task_type}) completed successfully.")
        return f"Task {task_id} ({task.task_type}) completed successfully."

    except Exception as e:
        logger.error(f"An error occurred while executing Task ID {task_id}: {e}", exc_info=True)
        try:
            task = Task.objects.get(pk=task_id)
            # [修改] 使用 FSM 的 'fail' 转换
            task.fail(error_message=str(e))
            task.save()
        except Task.DoesNotExist:
            pass
        return f"Task {task_id} failed."


def _handle_narration_generation(task: Task) -> dict:
    """
    [重构后] 处理“生成解说词”任务的核心逻辑。
    """
    logger.info(f"Starting NARRATION GENERATION for Task ID: {task.id}...")

    payload = task.payload
    series_name = payload.get("series_name")
    series_id = payload.get("series_id")
    total_scene_count = payload.get("total_scene_count")
    service_params = payload.get("service_params", {})
    output_file_path_str = payload.get("absolute_output_path")  # 由 View 注入

    if not total_scene_count or not isinstance(total_scene_count, int):
        # 如果 payload 中没有，抛出异常，强制上游编排器修复
        raise ValueError(
            "Payload is missing 'total_scene_count' or it is not an integer. RAG deployment must precede this.")
    if not all([series_name, series_id, output_file_path_str]):
        raise ValueError(
            "Payload for GENERATE_NARRATION is missing required keys: series_name, series_id, absolute_output_path.")

    # --- [新增] 1. 加载 AI 推理配置 ---
    config_path = settings.BASE_DIR / 'ai_services' / 'configs' / 'ai_inference_config.yaml'
    try:
        with config_path.open('r', encoding='utf-8') as f:
            ai_config_full = yaml.safe_load(f)
        # 提取 narration_generator 块的默认值
        ai_config = ai_config_full.get('narration_generator', {})
    except FileNotFoundError:
        logger.error(f"AI 配置 YAML 文件未找到: {config_path}")
        raise
    except KeyError:
        logger.error(f"AI 配置 YAML 文件中缺少 'narration_generator' 块。")
        raise
    # --- [新增结束] ---

    logger.info("Assembling dependencies for NarrationGenerator service...")
    instance_id = task.organization.name
    corpus_display_name = f"{series_id}-{instance_id}"

    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        # [修改] 调试日志应转到 LOG_ROOT
        debug_dir=settings.SHARED_LOG_ROOT / f"narration_task_{task.id}_debug"
    )

    narration_service = NarrationGenerator(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        prompts_dir=settings.BASE_DIR / 'ai_services' / 'narration' / 'prompts',
        logger=logger,
        # [不变] 工作目录应转到 TMP_ROOT
        work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
        gemini_processor=gemini_processor
    )

    # --- [修改] 2. 合并配置并调用服务 ---
    # 默认配置 (ai_config) 复制，然后用用户参数 (service_params) 覆盖，确保用户传入的参数优先级最高
    final_params = ai_config.copy()
    final_params.update(service_params)
    logger.info(f"Final AI inference parameters: {final_params}")

    result_data = narration_service.execute(
        series_name=series_name,
        corpus_display_name=corpus_display_name,
        total_scene_count=total_scene_count,
        **final_params  # <-- 传入合并后的参数
    )

    # [不变] View 提供了正确的 'absolute_output_path' (在 TMP_ROOT 中)
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"NARRATION GENERATION finished. Output saved to: {output_file_path}")

    return {
        "message": "Narration script generated successfully.",
        # [修改] 返回相对于 SHARED_ROOT 的路径
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

    instance_id = task.organization.name
    series_id = payload.get("series_id")
    corpus_display_name = f"{series_id}-{instance_id}"

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
        instance_id=instance_id
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


def _handle_character_metrics_calculation(task: Task) -> dict:
    """
    处理“角色量化指标计算”任务的核心逻辑。
    """
    logger.info(f"Starting CHARACTER METRICS for Task ID: {task.id}...")

    payload = task.payload
    # [修改] 键名现在由 View 自动生成
    input_file_path_str = payload.get("absolute_input_file_path")
    output_file_path_str = payload.get("absolute_output_path") # 由 View 注入
    service_params = payload.get("service_params", {})

    if not all([input_file_path_str, output_file_path_str]):
        raise ValueError("Payload for CHARACTER_METRICS is missing required absolute paths.")

    input_file_path = Path(input_file_path_str)
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    with input_file_path.open('r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
    logger.info("Blueprint data loaded from file.")

    calculator_service = CharacterMetricsCalculator(logger=logger)
    result_data = calculator_service.execute(
        blueprint_data=blueprint_data,
        **service_params
    )

    # [不变] View 提供了正确的 'absolute_output_path' (在 TMP_ROOT 中)
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER METRICS finished. Output saved to: {output_file_path}")

    return {
        "message": "Task completed successfully.",
        # [修改] 返回相对于 SHARED_ROOT 的路径
        "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        "metrics_summary": {
            "total_characters_found": len(result_data.get("all_characters_found", [])),
            "top_character": result_data.get("ranked_characters", [{}])[0].get("name", "N/A")
        }
    }


def _handle_character_pipeline(task: Task) -> dict:
    """
    执行完整的“角色分析线”编排任务。
    """
    logger.info(f"Starting CHARACTER PIPELINE for Task ID: {task.id}...")

    payload = task.payload
    mode = payload.get('mode')
    # [修改] 键名现在由 View 自动生成
    input_file_path_str = payload.get("absolute_input_file_path")
    output_file_path_str = payload.get("absolute_output_path") # 由 View 注入
    service_params = payload.get("service_params", {})

    # [修改] 允许 service_params 为空字典
    if not all([input_file_path_str, output_file_path_str]) or service_params is None:
        raise ValueError("Payload for PIPELINE is missing required absolute paths or service_params.")

    input_file_path = Path(input_file_path_str)
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    with input_file_path.open('r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
    logger.info("Blueprint data loaded from file for pipeline.")

    characters_to_process = []
    metrics_report = None

    if mode == 'specific':
        characters_to_process = payload.get('characters_to_analyze', [])
        logger.info(f"Pipeline running in 'specific' mode for characters: {characters_to_process}")

    elif mode == 'threshold':
        logger.info("Pipeline running in 'threshold' mode. Calculating metrics first...")
        # 实例化并执行指标计算器
        metrics_calculator = CharacterMetricsCalculator(logger=logger)
        metrics_report = metrics_calculator.execute(blueprint_data=blueprint_data, **service_params)

        threshold_config = payload.get('threshold', {})
        if 'top_n' in threshold_config:
            top_n = int(threshold_config['top_n'])
            ranked_characters = metrics_report.get("ranked_characters", [])
            characters_to_process = [char['name'] for char in ranked_characters[:top_n]]
            logger.info(f"Selected top {top_n} characters based on metrics: {characters_to_process}")
        elif 'min_score' in threshold_config:
            min_score = float(threshold_config['min_score'])
            scores = metrics_report.get("importance_scores", {})
            characters_to_process = [name for name, score in scores.items() if score >= min_score]
            logger.info(f"Selected characters with score >= {min_score}: {characters_to_process}")

    if not characters_to_process:
        logger.warning("No characters selected for identification. Pipeline will stop.")
        # [修改] 将结果保存到输出文件
        output_file_path = Path(output_file_path_str)
        result_to_save = {"message": "No characters met the criteria for fact identification.",
                          "metrics_report": metrics_report}
        with output_file_path.open('w', encoding='utf-8') as f:
            json.dump(result_to_save, f, ensure_ascii=False, indent=2)

        return {
            "message": "No characters met the criteria for fact identification.",
            "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        }

    logger.info(f"Proceeding with fact identification for {len(characters_to_process)} characters...")
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

    service_params['characters_to_analyze'] = characters_to_process
    id_service_response = identifier_service.execute(
        enhanced_script_path=input_file_path,
        **service_params
    )

    if id_service_response.get("status") != "success":
        raise RuntimeError(f"CharacterIdentifier service failed within the pipeline: {id_service_response}")

    result_to_save = id_service_response.get("data", {}).get("result", {})
    usage_report = id_service_response.get("data", {}).get("usage", {})

    # [不变] View 提供了正确的 'absolute_output_path' (在 TMP_ROOT 中)
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        result_to_save['metrics_calculation_results'] = metrics_report
        json.dump(result_to_save, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER PIPELINE finished. Final output saved to: {output_file_path}")

    return {
        "message": "Character pipeline completed successfully.",
        # [修改] 返回相对于 SHARED_ROOT 的路径
        "output_file_path": str(Path(settings.SHARED_TMP_ROOT.name) / output_file_path.name),
        "usage_report": usage_report,
        "characters_processed": characters_to_process
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
    """
    logger.info(f"Starting DUBBING GENERATION for Task ID: {task.id}...")

    payload = task.payload
    # 1. 从 payload 中获取路径和参数
    narration_path_str = payload.get("absolute_input_narration_path")
    output_file_path_str = payload.get("absolute_output_path")  # 这是最终JSON脚本的路径
    service_params = payload.get("service_params", {})

    # [核心修正]
    # 使用 .pop() 来提取 'template_name'。
    # 这会从 service_params 字典中【移除】该键，
    # 从而避免在 **service_params 解包时重复传递。
    template_name = service_params.pop("template_name", None)

    # 现在我们的检查逻辑是正确的
    if not all([narration_path_str, output_file_path_str, template_name]):
        raise ValueError("Payload for GENERATE_DUBBING is missing required keys: "
                         "absolute_input_narration_path, absolute_output_path, or "
                         "service_params.template_name")

    narration_path = Path(narration_path_str)
    output_json_path = Path(output_file_path_str)  # 最终的 JSON 脚本路径

    # 2. 为此任务创建专属的音频文件输出目录
    audio_work_dir = settings.SHARED_TMP_ROOT / f"dubbing_task_{task.id}_audio"
    audio_work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Audio files will be saved to: {audio_work_dir}")

    # 3. 组装依赖 (Composition Root)
    logger.info("Assembling dependencies for DubbingEngine service...")

    # 3.1 加载 YAML 模板
    templates_config_path = settings.BASE_DIR / 'ai_services' / 'dubbing' / 'configs' / 'dubbing_templates.yaml'
    with templates_config_path.open('r', encoding='utf-8') as f:
        all_templates = yaml.safe_load(f)

    # 3.2 实例化所需的策略 (按需添加)
    strategy_paieas = AliyunPAIEASStrategy(
        service_url=settings.PAI_EAS_SERVICE_URL,
        token=settings.PAI_EAS_TOKEN
    )

    available_strategies: Dict[str, TTSStrategy] = {
        "aliyun_paieas": strategy_paieas
    }

    # 4. 实例化 DubbingEngine 服务并注入所有依赖
    dubbing_service = DubbingEngine(
        logger=logger,
        work_dir=audio_work_dir,  # 注入音频工作目录
        strategies=available_strategies,
        templates=all_templates,
        shared_root_path=settings.SHARED_ROOT  # 注入共享根目录
    )

    # 5. 执行服务
    #    [核心修正] 现在的 **service_params 不再包含 'template_name'
    result_data = dubbing_service.execute(
        narration_path=narration_path,
        template_name=template_name,  # 显式传递
        **service_params  # 安全解包（只包含其他参数，如果有的话）
    )

    # 6. 将服务返回的 JSON 脚本保存到任务指定的输出路径
    with output_json_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"DUBBING GENERATION finished. Output JSON script saved to: {output_json_path}")

    # 7. 返回 Celery Task 的最终结果 (存入 Task.result 字段)
    return {
        "message": "Dubbing script and audio files generated successfully.",
        "output_file_path": str(output_json_path.relative_to(settings.SHARED_ROOT)),
        "audio_output_directory": str(audio_work_dir.relative_to(settings.SHARED_ROOT)),
        "total_clips_generated": len(result_data.get("dubbing_script", []))
    }