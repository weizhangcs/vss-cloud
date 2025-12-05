# task_manager/views.py
import uuid
from pathlib import Path

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import get_object_or_404

from django.conf import settings

from .authentication import EdgeInstanceAuthentication
from .models import Task
from .serializers import (
    TaskCreateSerializer,
    TaskCreateResponseSerializer,
    TaskDetailSerializer
)

# [新增] 引入自定义权限和异常
from core.permissions import IsEdgeAuthenticated
from core.exceptions import BizException
from core.error_codes import ErrorCode


class TaskCreateView(APIView):
    """
    [修改] 简化后的任务创建视图。
    """
    authentication_classes = [EdgeInstanceAuthentication]
    # [核心修改] 显式指定权限，绕过全局的 IsAuthenticated
    permission_classes = [IsEdgeAuthenticated]

    def post(self, request, *args, **kwargs):
        create_serializer = TaskCreateSerializer(data=request.data)

        # [优化] 使用 raise_exception=True 让全局 Handler 接管验证错误
        # 这会自动返回标准的 {code: 1001, message: "Invalid parameters", data: ...}
        if not create_serializer.is_valid(raise_exception=False):
            # 如果您想保留自定义的 400 返回，可以维持原样，
            # 但建议抛出异常以统一格式：
            # raise BizException(ErrorCode.INVALID_PARAM, data=create_serializer.errors)
            return Response(create_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = create_serializer.validated_data
        task_payload = validated_data.get('payload', {})
        task_type = validated_data['task_type']

        # 1. 自动查找并验证所有输入路径
        absolute_paths_to_add = {}
        for key, value in task_payload.items():
            if key.endswith("_path"):
                if not isinstance(value, str):
                    # [优化] 抛出标准业务异常
                    raise BizException(ErrorCode.INVALID_PARAM, msg=f"Path '{key}' must be a string.")

                relative_path = value
                absolute_input_path = settings.SHARED_ROOT / relative_path

                if not absolute_input_path.is_file():
                    # [优化] 抛出标准 NotFound 异常
                    raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Input file not found at: {relative_path}",
                                       status_code=404)

                absolute_paths_to_add[f'absolute_{key}'] = str(absolute_input_path)

        task_payload.update(absolute_paths_to_add)

        # 2. 定义所有任务的输出文件前缀
        output_prefixes = {
            Task.TaskType.CHARACTER_IDENTIFIER: "character_facts",
            Task.TaskType.DEPLOY_RAG_CORPUS: "rag_deployment_report",
            Task.TaskType.GENERATE_NARRATION: "narration_script",
            Task.TaskType.GENERATE_EDITING_SCRIPT: "editing_script",
            Task.TaskType.GENERATE_DUBBING: "dubbing_script",
            Task.TaskType.LOCALIZE_NARRATION: "localized_script"
        }

        output_prefix = output_prefixes.get(task_type)
        if output_prefix:
            output_filename = f"{output_prefix}_{uuid.uuid4()}.json"
            absolute_output_path = settings.SHARED_TMP_ROOT / output_filename
            task_payload['absolute_output_path'] = str(absolute_output_path)

            # 3. 保存时增加 assigned_edge
            try:
                task = create_serializer.save(
                    organization=request.auth.organization,
                    assigned_edge=request.auth,
                    payload=task_payload
                )
            except Exception as e:
                # [优化] 捕获数据库保存错误
                raise BizException(ErrorCode.TASK_CREATION_FAILED, msg=str(e))

        response_serializer = TaskCreateResponseSerializer(task)
        return Response(response_serializer.data, status=status.HTTP_202_ACCEPTED)


class TaskDetailView(APIView):
    """
    API 端点，允许已认证的边缘实例查询特定云原生任务的状态和结果。
    """
    authentication_classes = [EdgeInstanceAuthentication]
    # [核心修改] 添加权限
    permission_classes = [IsEdgeAuthenticated]

    def get(self, request, task_id, *args, **kwargs):
        edge_instance = request.auth
        # 使用 get_object_or_404，如果找不到会自动抛出 Http404，
        # 全局 Handler 会将其捕获并转为标准的 Resource Not Found (Code 1004)
        queryset = Task.objects.filter(organization=edge_instance.organization)
        task = get_object_or_404(queryset, pk=task_id)

        serializer = TaskDetailSerializer(task, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)