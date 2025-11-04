# task_manager/views.py
import uuid
import os
from pathlib import Path
from django.http import FileResponse, Http404
from django.db import transaction
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import get_object_or_404 # 导入一个便捷的辅助函数
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from .authentication import EdgeInstanceAuthentication
from .models import Task
from .serializers import TaskFetchSerializer, TaskCreateSerializer, TaskCreateResponseSerializer, TaskDetailSerializer, FileUploadSerializer


class FetchAssignedTasksView(APIView):
    """
    API 端点，允许已认证的边缘实例拉取分配给它的新任务。
    """
    # 使用我们刚刚创建的自定义认证类
    authentication_classes = [EdgeInstanceAuthentication]

    # 我们可以添加权限，确保请求是经过认证的
    # permission_classes = [IsAuthenticated] # 暂时注释，因为我们返回的 user 是 None

    def get(self, request, *args, **kwargs):
        """
        处理 GET 请求，用于拉取任务。
        """
        # 由于认证成功，我们可以从 request.auth 中获取 edge_instance 对象
        edge_instance = request.auth

        # 定义一次最多拉取多少个任务
        FETCH_LIMIT = 5

        with transaction.atomic():
            # 使用数据库事务来保证数据一致性

            # 1. 查询属于该组织、且状态为 PENDING 的任务
            #    - select_for_update(skip_locked=True) 是关键：
            #      它会锁定查询到的行，防止其他并发请求处理这些任务。
            #      如果行已被其他事务锁定，skip_locked=True 会让查询跳过这些行，
            #      而不是等待锁释放，从而避免死锁和请求超时。
            pending_tasks = Task.objects.select_for_update(skip_locked=True).filter(
                organization=edge_instance.organization,
                status=Task.TaskStatus.PENDING
            ).order_by('created')[:FETCH_LIMIT]

            if not pending_tasks.exists():
                # 如果没有待处理的任务，返回一个空列表
                return Response([], status=status.HTTP_200_OK)

            # 2. 将这些任务的状态从 PENDING 更新为 ASSIGNED
            tasks_to_assign = []
            for task in pending_tasks:
                # 调用我们模型中定义的 FSM 状态转换方法
                task.assign_to_edge(edge_instance=edge_instance)
                task.save()
                tasks_to_assign.append(task)

        # 3. 将已分配的任务序列化后返回给边缘实例
        serializer = TaskFetchSerializer(tasks_to_assign, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TaskCreateView(APIView):
    authentication_classes = [EdgeInstanceAuthentication]

    def post(self, request, *args, **kwargs):
        create_serializer = TaskCreateSerializer(data=request.data)
        if not create_serializer.is_valid():
            return Response(create_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = create_serializer.validated_data
        task_payload = validated_data.get('payload', {})
        task_type = validated_data['task_type']

        # --- [核心升级] 使用一个更全面的IO配置映射表 ---
        # 结构: { 任务类型: {"inputs": [输入键列表], "output_prefix": "输出文件前缀"} }
        # "inputs": [] 表示该任务没有来自共享卷的输入文件。
        tasks_io_config = {
            Task.TaskType.CHARACTER_IDENTIFIER: {"inputs": ["input_file_path"], "output_prefix": "character_facts"},
            Task.TaskType.CHARACTER_METRICS: {"inputs": ["input_file_path"], "output_prefix": "character_metrics"},
            Task.TaskType.CHARACTER_PIPELINE: {"inputs": ["input_file_path"],
                                               "output_prefix": "character_pipeline_results"},
            Task.TaskType.DEPLOY_RAG_CORPUS: {"inputs": [], "output_prefix": "rag_deployment_report"},
            # RAG部署从GCS读取，但会产生报告
            Task.TaskType.GENERATE_NARRATION: {"inputs": [], "output_prefix": "narration_script"},
            Task.TaskType.GENERATE_EDITING_SCRIPT: {
                "inputs": ["dubbing_script_path", "blueprint_path"],
                "output_prefix": "editing_script"
            }
        }

        if task_type in tasks_io_config:
            config = tasks_io_config[task_type]
            input_keys = config.get("inputs", [])
            output_prefix = config.get("output_prefix")

            # 1. [统一逻辑] 验证并构建所有输入文件的绝对路径
            if input_keys:
                for key in input_keys:
                    relative_path = task_payload.get(key)
                    if not relative_path:
                        return Response({"error": f"Payload for {task_type} must contain '{key}'"},
                                        status=status.HTTP_400_BAD_REQUEST)

                    absolute_input_path = settings.SHARED_RESOURCE_ROOT / relative_path

                    if not absolute_input_path.is_file():
                        return Response({"error": f"Input file not found at: {absolute_input_path}"},
                                        status=status.HTTP_404_NOT_FOUND)

                    task_payload[f'absolute_{key}'] = str(absolute_input_path)

            # 2. [统一逻辑] 构建唯一的绝对输出路径
            if output_prefix:
                output_filename = f"{output_prefix}_{uuid.uuid4()}.json"
                absolute_output_path = settings.SHARED_OUTPUT_ROOT / output_filename
                task_payload['absolute_output_path'] = str(absolute_output_path)

        # 使用更新后的 payload 创建任务
        task = create_serializer.save(
            organization=request.auth.organization,
            payload=task_payload
        )

        response_serializer = TaskCreateResponseSerializer(task)
        return Response(response_serializer.data, status=status.HTTP_202_ACCEPTED)


class TaskDetailView(APIView):
    """
    API 端点，允许已认证的边缘实例查询特定云原生任务的状态和结果。
    """
    # 同样，复用我们的自定义认证类
    authentication_classes = [EdgeInstanceAuthentication]

    # permission_classes = [IsAuthenticated]

    def get(self, request, task_id, *args, **kwargs):
        """
        处理 GET /api/v1/tasks/{task_id}/ 请求。
        """
        # 从认证信息中获取边缘实例
        edge_instance = request.auth

        # 准备查询集：只包含属于该边缘实例所属组织的 Task
        queryset = Task.objects.filter(organization=edge_instance.organization)

        # 使用 get_object_or_404 辅助函数来安全地获取任务。
        # 它会尝试从 queryset 中查找 pk=task_id 的对象。
        # - 如果找到，就返回该对象。
        # - 如果找不到（无论是任务不存在，还是任务不属于该组织），
        #   它会自动抛出一个 Http404 异常，DRF会将其转换为一个 404 Not Found 响应。
        # 这优雅地实现了我们的安全要求：一个租户绝对不能查询到另一个租户的任务。
        task = get_object_or_404(queryset, pk=task_id)

        # 使用 DetailSerializer 来格式化返回数据
        serializer = TaskDetailSerializer(task, context={'request': request})

        return Response(serializer.data, status=status.HTTP_200_OK)


class TaskResultDownloadView(APIView):
    """
    允许已认证的边缘实例下载已完成任务的结果文件。
    """
    authentication_classes = [EdgeInstanceAuthentication]

    def get(self, request, task_id, *args, **kwargs):
        edge_instance = request.auth

        # 1. 验证任务归属
        queryset = Task.objects.filter(organization=edge_instance.organization)
        task = get_object_or_404(queryset, pk=task_id)

        # 2. 检查任务状态
        if task.status != Task.TaskStatus.COMPLETED:
            # 任务未完成，返回 "Too Early" 或 "Not Found"
            return Response(
                {"error": "Task is not yet completed."},
                status=status.HTTP_425_TOO_EARLY
            )

        # 3. 从 result 字段获取路径
        try:
            file_path_str = task.result.get("output_file_path")
            if not file_path_str:
                raise ValueError("output_file_path not found in task result.")

            file_path = Path(file_path_str)

            # 4. 验证文件是否存在（得益于共享卷）
            if not file_path.is_file():
                # 文件在记录中存在，但在磁盘上丢失了
                raise Http404("Result file not found on server.")

            # 5. 使用 FileResponse 高效流式传输文件
            response = FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)
            return response

        except (ValueError, TypeError, Http404) as e:
            return Response(
                {"error": f"Could not retrieve file: {e}"},
                status=status.HTTP_404_NOT_FOUND
            )

class FileUploadView(APIView):
    """
    一个专用的端点，用于将文件上传到共享资源目录。
    """
    authentication_classes = [EdgeInstanceAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = FileUploadSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 1. 获取上传的文件对象
        uploaded_file = serializer.validated_data['file']

        # 2. 定义存储位置 (使用 settings.py 中的 SHARED_RESOURCE_ROOT)
        # 我们将文件保存到“资源”目录中，供 Celery 任务读取
        fs = FileSystemStorage(location=settings.SHARED_RESOURCE_ROOT)

        # 3. 创建一个唯一的、安全的文件名
        # (我们使用 UUID + 原始文件名来防止冲突和路径遍历)
        unique_filename = f"{uuid.uuid4()}_{uploaded_file.name}"

        try:
            # 4. 保存文件到共享卷
            saved_name = fs.save(unique_filename, uploaded_file)

            # 5. 返回 *相对* 路径，供 TaskCreateView 使用
            # (saved_name 已经是相对 SHARED_RESOURCE_ROOT 的路径)
            return Response(
                {"relative_path": saved_name},
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {"error": f"Could not save file: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )