# task_manager/api.py
import uuid
from pathlib import Path
from typing import Dict, Any

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse
from ninja import NinjaAPI, Router
from ninja.errors import HttpError

from .models import Task
from core.auth import EdgeAuth
from .schemas import TaskCreateRequest, TaskResponse
from core.error_codes import ErrorCode  # 假设你保留了错误码定义

# 如果你想把 task 相关接口作为一个独立模块路由
router = Router(auth=EdgeAuth())


@router.post("/", response={202: Dict[str, Any], 400: Dict[str, Any]})
def create_task(request, data: TaskCreateRequest):
    """
    创建云原生任务
    """
    edge_instance = request.auth
    task_payload = data.payload

    # --- [逻辑移植] 1. 路径自动补全与校验 ---
    # 这部分逻辑是从原 Serializer/View 中移植过来的，保持了对相对路径的处理
    absolute_paths_to_add = {}

    for key, value in task_payload.items():
        if key.endswith("_path"):
            if not isinstance(value, str):
                raise HttpError(400, f"Path param '{key}' must be a string.")

            relative_path = value
            # 假设 settings.SHARED_ROOT 是 Path 对象
            absolute_input_path = settings.SHARED_ROOT / relative_path

            if not absolute_input_path.is_file():
                # 抛出 404 或 400 均可，这里用 400 明确参数错误
                raise HttpError(400, f"Input file not found at: {relative_path}")

            absolute_paths_to_add[f'absolute_{key}'] = str(absolute_input_path)

    task_payload.update(absolute_paths_to_add)

    # --- [逻辑移植] 2. 输出路径生成 ---
    output_prefixes = {
        Task.TaskType.CHARACTER_IDENTIFIER.value: "character_facts",
        Task.TaskType.DEPLOY_RAG_CORPUS.value: "rag_deployment_report",
        Task.TaskType.GENERATE_NARRATION.value: "narration_script",
        Task.TaskType.GENERATE_EDITING_SCRIPT.value: "editing_script",
        Task.TaskType.GENERATE_DUBBING.value: "dubbing_script",
        Task.TaskType.LOCALIZE_NARRATION.value: "localized_script"
    }

    output_prefix = output_prefixes.get(data.task_type)

    if output_prefix:
        output_filename = f"{output_prefix}_{uuid.uuid4()}.json"
        absolute_output_path = settings.SHARED_TMP_ROOT / output_filename
        task_payload['absolute_output_path'] = str(absolute_output_path)

    # --- 3. 任务创建 ---
    try:
        task = Task.objects.create(
            organization=edge_instance.organization,
            assigned_edge=edge_instance,
            task_type=data.task_type,
            payload=task_payload,
            status=Task.TaskStatus.PENDING
        )
        # Signal 会自动触发 Celery 任务，这里无需手动调用
    except Exception as e:
        raise HttpError(500, f"Task creation failed: {str(e)}")

    return 202, {
        "id": task.id,
        "status": task.status,
        "message": "Task accepted for processing."
    }


@router.get("/{task_id}", response=TaskResponse)
def get_task_detail(request, task_id: int):
    """
    查询任务详情
    """
    edge_instance = request.auth

    # 确保只能查自己组织的任务
    task = get_object_or_404(
        Task,
        pk=task_id,
        organization=edge_instance.organization
    )

    # --- [逻辑移植] 计算 download_url ---
    download_url = None
    if task.status == Task.TaskStatus.COMPLETED and task.result and task.result.get("output_file_path"):
        path = reverse('vss_api:task_download', kwargs={'task_id': task.id})
        download_url = request.build_absolute_uri(path)

    # 构造响应对象
    # Ninja 会自动将 Task ORM 对象的数据填充到 Schema 中
    # 对于 Schema 中有但 Model 中没有的字段 (download_url)，需要手动赋值
    return TaskResponse(
        id=task.id,
        status=task.status,
        task_type=task.task_type,
        result=task.result,
        created=task.created,
        modified=task.modified,
        download_url=download_url
    )