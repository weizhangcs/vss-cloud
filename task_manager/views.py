# task_manager/views.py
import os
from pathlib import Path

from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from .authentication import EdgeInstanceAuthentication
from .models import Task
from .serializers import (
    # [修改] 移除 TaskFetchSerializer
    TaskCreateSerializer,
    TaskCreateResponseSerializer,
    TaskDetailSerializer
)
from rest_framework.generics import get_object_or_404
from django.conf import settings
import uuid

# [修改] FetchAssignedTasksView 已删除

class TaskCreateView(APIView):
    """
    [修改] 简化后的任务创建视图。
    """
    authentication_classes = [EdgeInstanceAuthentication]

    def post(self, request, *args, **kwargs):
        create_serializer = TaskCreateSerializer(data=request.data)

        if not create_serializer.is_valid():
            return Response(create_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = create_serializer.validated_data
        task_payload = validated_data.get('payload', {})
        task_type = validated_data['task_type']

        # 1. 自动查找并验证所有输入路径
        absolute_paths_to_add = {}
        for key, value in task_payload.items():
            if key.endswith("_path"):
                if not isinstance(value, str):
                    return Response({"error": f"Path '{key}' must be a string."}, status=status.HTTP_400_BAD_REQUEST)

                relative_path = value
                absolute_input_path = settings.SHARED_ROOT / relative_path

                if not absolute_input_path.is_file():
                    return Response({"error": f"Input file not found at: {absolute_input_path}"},
                                    status=status.HTTP_404_NOT_FOUND)

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

            # 3. [关键修正] 保存时增加 assigned_edge
            task = create_serializer.save(
                organization=request.auth.organization,
                assigned_edge=request.auth,  # <--- 新增：记录是哪个 Edge 发起的
                payload=task_payload
            )
        response_serializer = TaskCreateResponseSerializer(task)
        return Response(response_serializer.data, status=status.HTTP_202_ACCEPTED)


class TaskDetailView(APIView):
    """
    API 端点，允许已认证的边缘实例查询特定云原生任务的状态和结果。
    """
    authentication_classes = [EdgeInstanceAuthentication]

    def get(self, request, task_id, *args, **kwargs):
        edge_instance = request.auth
        queryset = Task.objects.filter(organization=edge_instance.organization)
        task = get_object_or_404(queryset, pk=task_id)
        serializer = TaskDetailSerializer(task, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

# TaskResultDownloadView 已移至 utils/views.py，此处不再需要