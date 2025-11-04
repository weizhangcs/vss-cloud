# task_manager/tasks.py
import time
import tempfile
import json
from pathlib import Path

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
from ai_services.common.gemini.cost_calculator_v2 import CostCalculator
from ai_services.narration.narration_generator import NarrationGenerator
from ai_services.editing.broll_selector_service import BrollSelectorService

# 2. 获取一个 Celery Task 专用的 logger 实例
logger = get_task_logger(__name__)


@shared_task
def execute_cloud_native_task(task_id):
    """
    Celery 任务，使用专业的日志记录。
    """
    # 3. 在任务的最开始，立刻打印一条日志。
    #    这可以帮助我们确认 worker 是否成功从 Redis 领取到了任务。
    logger.info(f"Celery worker successfully received task with ID: {task_id}")

    try:
        task = Task.objects.get(pk=task_id)
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
        elif task.task_type == Task.TaskType.CHARACTER_PIPELINE:  # <-- [新增] 调度到新的编排任务
            result = _handle_character_pipeline(task)
        elif task.task_type == Task.TaskType.GENERATE_EDITING_SCRIPT:  # <-- [新增]
            result = _handle_editing_script_generation(task)
        else:
            raise ValueError(f"Unsupported cloud-native task type: {task.task_type}")

        task.result = result
        task.status = Task.TaskStatus.COMPLETED
        task.save()
        logger.info(f"Task {task_id} ({task.task_type}) completed successfully.")
        return f"Task {task_id} ({task.task_type}) completed successfully."

    except Exception as e:
        logger.error(f"An error occurred while executing Task ID {task_id}: {e}", exc_info=True)
        try:
            task = Task.objects.get(pk=task_id)
            task.result = {"error": str(e)}
            task.status = Task.TaskStatus.FAILED
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
    # 从 payload 获取必要的运行时参数
    series_name = payload.get("series_name")
    series_id = payload.get("series_id")  # 我们用 series_id 来构建语料库名称
    output_file_path_str = payload.get("absolute_output_path")
    service_params = payload.get("service_params", {})

    if not all([series_name, series_id, output_file_path_str]):
        raise ValueError(
            "Payload for GENERATE_NARRATION is missing required keys: series_name, series_id, absolute_output_path.")

    # --- 依赖组装 (Composition Root) ---
    logger.info("Assembling dependencies for NarrationGenerator service...")

    # 构建租户隔离的语料库名称
    instance_id = task.organization.name
    corpus_display_name = f"{series_id}-{instance_id}"

    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_debug"
    )

    # 实例化服务
    narration_service = NarrationGenerator(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        prompts_dir=settings.BASE_DIR / 'ai_services' / 'narration' / 'prompts',  # 使用新的独立路径
        logger=logger,
        work_dir=settings.SHARED_TMP_ROOT / f"narration_task_{task.id}_workspace",
        gemini_processor=gemini_processor
    )

    # --- 执行服务 ---
    result_data = narration_service.execute(
        series_name=series_name,
        corpus_display_name=corpus_display_name,
        **service_params
    )

    # --- 保存结果到文件 ---
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"NARRATION GENERATION finished. Output saved to: {output_file_path}")

    # --- 返回任务结果 ---
    return {
        "message": "Narration script generated successfully.",
        "output_file_path": str(output_file_path),
        "usage_report": result_data.get("ai_total_usage", {})
    }


def _handle_rag_deployment(task: Task) -> dict:
    """
    [最终重构版] 处理“部署RAG语料库”任务的核心逻辑。

    此函数现在直接从共享卷读取输入文件，并在处理完成后将输入和输出备份到GCS。
    """
    logger.info(f"Starting RAG DEPLOYMENT for Task ID: {task.id}...")

    payload = task.payload
    # 1. 从 payload 中读取由 view 准备好的【绝对路径】
    blueprint_path_str = payload.get("absolute_blueprint_input_path")
    facts_path_str = payload.get("absolute_facts_input_path")

    if not all([blueprint_path_str, facts_path_str]):
        raise ValueError("Payload for DEPLOY_RAG_CORPUS is missing required absolute paths.")

    local_blueprint_path = Path(blueprint_path_str)
    local_facts_path = Path(facts_path_str)

    # 2. [已移除] 不再需要从GCS下载文件的步骤

    # 使用一个与任务ID相关的、持久化的临时目录
    temp_dir = settings.SHARED_TMP_ROOT / f"rag_deploy_{task.id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # --- 3. 依赖组装 (与之前版本一致) ---
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

    # --- 4. 执行核心部署流程 (逻辑不变) ---
    # deployer 内部的逻辑本来就是处理本地文件，所以无需改动
    deployer_result = deployer.execute(
        corpus_display_name=corpus_display_name,
        blueprint_path=local_blueprint_path,
        facts_path=local_facts_path,
        gcs_bucket_name=settings.GCS_DEFAULT_BUCKET,
        staging_dir=temp_dir / "staging",
        instance_id=instance_id
    )

    # --- 5. [新增] 将输入文件备份到GCS ---
    # 为了可追溯性，我们将本次任务使用的输入文件归档到GCS。
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

    # 返回 deployer 的结果，其中包含了语料库名称和RAG源文件的GCS路径
    return deployer_result


def _handle_character_identification(task: Task) -> dict:
    """
    处理“人物事实识别”任务的核心逻辑。
    This function is the Composition Root inside the Celery worker.
    """
    logger.info(f"Starting CHARACTER IDENTIFIER for Task ID: {task.id}...")

    payload = task.payload
    # 1. 从 payload 中读取由 view 准备好的【绝对路径】
    input_file_path_str = payload.get("absolute_input_path")
    output_file_path_str = payload.get("absolute_output_path")
    service_params = payload.get("service_params", {})

    if not all([input_file_path_str, output_file_path_str, service_params]):
        raise ValueError("Payload is missing required absolute paths or service_params.")

    # --- Dependency Assembly (using Django settings) ---
    logger.info("Assembling dependencies for CharacterIdentifier service...")

    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=settings.SHARED_OUTPUT_ROOT / f"task_{task.id}_debug"
    )
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    # Define paths based on Django settings
    service_name = CharacterIdentifier.SERVICE_NAME
    prompts_dir = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'prompts'
    localization_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'localization' / f"{service_name}.json"
    schema_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'metadata' / "fact_attributes.json"
    #localization_path = settings.SHARED_RESOURCE_ROOT / "localization" / "analysis" / f"{service_name}.json"
    #schema_path = settings.SHARED_RESOURCE_ROOT / "metadata" / "fact_attributes.json"

    # Instantiate the service
    identifier_service = CharacterIdentifier(
        gemini_processor=gemini_processor,
        cost_calculator=cost_calculator,
        prompts_dir=prompts_dir,
        localization_path=localization_path,
        schema_path=schema_path,
        logger=logger,
        base_path=settings.SHARED_OUTPUT_ROOT / f"task_{task.id}_workspace"
    )

    # --- Execute Service ---
    # Note: This assumes the input file path is accessible by the worker.
    # We will address the shared storage issue separately.
    input_file_path = Path(input_file_path_str)

    # 确保文件存在于共享卷中
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    result_data = identifier_service.execute(
        enhanced_script_path=input_file_path,
        **service_params
    )

    # 4. [修正] 从返回的容器中，按正确的结构提取数据
    if result_data.get("status") != "success":
        raise RuntimeError(f"CharacterIdentifier service returned a non-success status: {result_data}")

    data_payload = result_data.get("data", {})
    result_to_save = data_payload.get("result", {})  # 这是要保存到文件的核心结果
    usage_report = data_payload.get("usage", {})  # 这是要存入任务结果的用量报告

    # 5. Worker 的新职责：将【提取出的核心结果】保存到共享输出文件中
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_to_save, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER IDENTIFIER finished. Output saved to: {output_file_path}")

    # 6. [修正] 任务的最终结果应该是对输出文件的引用，以及【提取出的用量报告】
    return {
        "message": "Task completed successfully.",
        "output_file_path": str(output_file_path),
        "usage_report": usage_report  # <-- 使用正确提取的用量报告
    }


# [核心修改] 更新 _handle_character_metrics_calculation 函数
def _handle_character_metrics_calculation(task: Task) -> dict:
    """
    处理“角色量化指标计算”任务的核心逻辑 (增加文件输出)。
    """
    logger.info(f"Starting CHARACTER METRICS for Task ID: {task.id}...")

    payload = task.payload
    # 1. 从 payload 中读取由 view 准备好的【绝对路径】
    input_file_path_str = payload.get("absolute_input_path")
    output_file_path_str = payload.get("absolute_output_path")  # <-- 新增读取
    service_params = payload.get("service_params", {})

    # 2. 更新验证逻辑
    if not all([input_file_path_str, output_file_path_str]):
        raise ValueError("Payload for CHARACTER_METRICS is missing required absolute paths.")

    # 3. Worker的文件IO和数据准备逻辑保持不变
    input_file_path = Path(input_file_path_str)
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    with input_file_path.open('r', encoding='utf-8') as f:
        blueprint_data = json.load(f)

    logger.info("Blueprint data loaded from file.")

    # 4. 依赖组装和执行服务的逻辑保持不变
    calculator_service = CharacterMetricsCalculator(logger=logger)
    result_data = calculator_service.execute(
        blueprint_data=blueprint_data,
        **service_params
    )

    # 5. [新增] Worker 的新职责：将结果保存到共享输出文件中
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER METRICS finished. Output saved to: {output_file_path}")

    # 6. [修改] 任务的最终结果应该是对输出文件的引用，以及关键的元数据
    #    这样与 character_identifier 的返回结构保持一致
    return {
        "message": "Task completed successfully.",
        "output_file_path": str(output_file_path),
        "metrics_summary": {
            "total_characters_found": len(result_data.get("all_characters_found", [])),
            "top_character": result_data.get("ranked_characters", [{}])[0].get("name", "N/A")
        }
    }


# [新增] 完整的编排任务处理函数
def _handle_character_pipeline(task: Task) -> dict:
    """
    执行完整的“角色分析线”编排任务。
    """
    logger.info(f"Starting CHARACTER PIPELINE for Task ID: {task.id}...")

    payload = task.payload
    mode = payload.get('mode')
    input_file_path_str = payload.get("absolute_input_path")
    output_file_path_str = payload.get("absolute_output_path")
    service_params = payload.get("service_params", {})

    if not all([input_file_path_str, output_file_path_str]):
        raise ValueError("Payload for PIPELINE is missing required absolute paths.")

    # --- 步骤 1: 加载共享的输入数据 ---
    input_file_path = Path(input_file_path_str)
    if not input_file_path.is_file():
        raise FileNotFoundError(f"Input file not found by worker at path: {input_file_path}")

    with input_file_path.open('r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
    logger.info("Blueprint data loaded from file for pipeline.")

    # --- 步骤 2: 决定要分析的角色列表 ---
    characters_to_process = []
    metrics_report = None  # 用于存储指标计算的中间结果

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
        return {"message": "No characters met the criteria for fact identification.", "metrics_report": metrics_report}

    # --- 步骤 3: 为筛选出的角色执行事实识别 ---
    logger.info(f"Proceeding with fact identification for {len(characters_to_process)} characters...")
    # 依赖组装 (与 _handle_character_identification 中一致)
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=settings.SHARED_OUTPUT_ROOT / f"task_{task.id}_debug"
    )
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    # Define paths based on Django settings
    service_name = CharacterIdentifier.SERVICE_NAME
    prompts_dir = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'prompts'
    localization_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'localization' / f"{service_name}.json"
    schema_path = settings.BASE_DIR / 'ai_services' / 'analysis' / 'character' / 'metadata' / "fact_attributes.json"


    # Instantiate the service
    identifier_service = CharacterIdentifier(
        gemini_processor=gemini_processor,
        cost_calculator=cost_calculator,
        prompts_dir=prompts_dir,
        localization_path=localization_path,
        schema_path=schema_path,
        logger=logger,
        base_path=settings.SHARED_OUTPUT_ROOT / f"task_{task.id}_workspace"
    )

    # 执行事实识别服务
    # 注意：我们将 'characters_to_analyze' 覆盖为我们筛选出的列表
    service_params['characters_to_analyze'] = characters_to_process
    id_service_response = identifier_service.execute(
        enhanced_script_path=input_file_path,
        **service_params
    )

    if id_service_response.get("status") != "success":
        raise RuntimeError(f"CharacterIdentifier service failed within the pipeline: {id_service_response}")

    # --- 步骤 4: 保存最终结果 ---
    result_to_save = id_service_response.get("data", {}).get("result", {})
    usage_report = id_service_response.get("data", {}).get("usage", {})

    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        # 为了报告的完整性，我们可以将指标计算结果也一并存入
        result_to_save['metrics_calculation_results'] = metrics_report
        json.dump(result_to_save, f, ensure_ascii=False, indent=2)

    logger.info(f"CHARACTER PIPELINE finished. Final output saved to: {output_file_path}")

    return {
        "message": "Character pipeline completed successfully.",
        "output_file_path": str(output_file_path),
        "usage_report": usage_report,
        "characters_processed": characters_to_process
    }


# [新增] 为新任务编写专门的处理函数
def _handle_editing_script_generation(task: Task) -> dict:
    """
    处理“生成剪辑脚本”任务的核心逻辑。
    """
    logger.info(f"Starting EDITING SCRIPT GENERATION for Task ID: {task.id}...")

    payload = task.payload
    # 从 payload 中读取由 view 准备好的【两个输入】和【一个输出】的绝对路径
    dubbing_path_str = payload.get("absolute_dubbing_script_path")
    blueprint_path_str = payload.get("absolute_blueprint_path")
    output_file_path_str = payload.get("absolute_output_path")
    service_params = payload.get("service_params", {})

    if not all([dubbing_path_str, blueprint_path_str, output_file_path_str]):
        raise ValueError("Payload for GENERATE_EDITING_SCRIPT is missing required absolute paths.")

    # --- 依赖组装 (Composition Root) ---
    logger.info("Assembling dependencies for BrollSelectorService...")
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=settings.SHARED_OUTPUT_ROOT / f"task_{task.id}_debug"
    )
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    selector_service = BrollSelectorService(
        prompts_dir=settings.BASE_DIR / 'ai_services' / 'editing' / 'prompts',
        logger=logger,
        work_dir=settings.SHARED_TMP_ROOT / f"editing_task_{task.id}_workspace",
        gemini_processor=gemini_processor
    )

    # --- 执行服务 ---
    result_data = selector_service.execute(
        dubbing_path=Path(dubbing_path_str),
        blueprint_path=Path(blueprint_path_str),
        **service_params
    )

    # --- 保存结果到文件 ---
    output_file_path = Path(output_file_path_str)
    with output_file_path.open('w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info(f"EDITING SCRIPT GENERATION finished. Output saved to: {output_file_path}")

    # --- 返回任务结果 ---
    return {
        "message": "Editing script generated successfully.",
        "output_file_path": str(output_file_path),
        "script_summary": {
            "total_sequences": len(result_data.get("editing_script", []))
        }
    }