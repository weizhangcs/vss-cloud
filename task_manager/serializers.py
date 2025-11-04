# task_manager/serializers.py
from django.urls import reverse
from rest_framework import serializers
from .models import Task
from organization.models import Organization

class TaskFetchSerializer(serializers.ModelSerializer):
    """
    用于格式化返回给边缘实例的任务数据的 Serializer。
    """
    class Meta:
        model = Task
        # 只包含边缘执行任务所需的最小信息集
        fields = [
            'id',          # 任务的唯一ID
            'task_type',   # 任务类型 (例如: RUN_DUBBING)
            'payload'      # 任务的具体参数
        ]

class TaskCreateSerializer(serializers.ModelSerializer):
    """
    用于验证从边缘传入的云原生任务创建请求的 Serializer。
    """
    class Meta:
        model = Task
        # 边缘在创建任务时，只需要提供这两个字段
        fields = ['task_type', 'payload']

    def validate_task_type(self, value):
        """
        验证钩子：确保边缘只能创建“云原生”类型的任务。
        我们不希望边缘意外地创建一个它自己应该执行的任务（如 RUN_DUBBING）。
        """
        cloud_native_tasks = [
            Task.TaskType.CHARACTER_METRICS,
            Task.TaskType.CHARACTER_IDENTIFIER,
            Task.TaskType.CHARACTER_PIPELINE,
            Task.TaskType.DEPLOY_RAG_CORPUS,
            Task.TaskType.GENERATE_NARRATION,
            Task.TaskType.GENERATE_EDITING_SCRIPT
        ]  # 这是一个白名单
        if value not in cloud_native_tasks:
            raise serializers.ValidationError(
                f"Invalid task_type. Only the following types can be created via API: {cloud_native_tasks}"
            )
        return value

    # [新增] 为 CHARACTER_PIPELINE 任务添加专属的 payload 验证逻辑
    def validate(self, data):
        """
        对整个数据对象进行验证，特别是针对不同任务类型的 payload 结构。
        """
        if data.get('task_type') == Task.TaskType.CHARACTER_PIPELINE:
            payload = data.get('payload', {})
            mode = payload.get('mode')

            if mode == 'specific':
                if 'characters_to_analyze' not in payload:
                    raise serializers.ValidationError(
                        "For 'specific' mode, 'characters_to_analyze' is required in payload.")
            elif mode == 'threshold':
                if 'threshold' not in payload or not isinstance(payload['threshold'], dict):
                    raise serializers.ValidationError(
                        "For 'threshold' mode, a 'threshold' object is required in payload.")
                if 'top_n' not in payload['threshold'] and 'min_score' not in payload['threshold']:
                    raise serializers.ValidationError(
                        "The 'threshold' object must contain either 'top_n' or 'min_score'.")
            else:
                raise serializers.ValidationError(
                    "Payload for CHARACTER_PIPELINE must include a 'mode' ('specific' or 'threshold').")

        return data

class TaskCreateResponseSerializer(serializers.ModelSerializer):
    """
    用于格式化返回给边缘的“任务受理回执”的 Serializer。
    """
    # 我们添加一个自定义字段，以提供更友好的消息
    message = serializers.CharField(default="Task accepted for processing.")

    class Meta:
        model = Task
        # 返回给边缘的信息应简洁明了：任务ID和当前状态
        fields = ['id', 'status', 'message']
        read_only_fields = fields


class TaskDetailSerializer(serializers.ModelSerializer):
    """
    [FINAL VERSION]
    """

    # 1. [UNCHANGED] Field is declared on the serializer
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Task

        # 2. [FIXED] 'download_url' MUST be included here to satisfy
        #    the AssertionError.
        fields = [
            'id',
            'status',
            'task_type',
            'result',
            'created',
            'modified',
            'download_url'  # <-- ADDED BACK
        ]

        # 3. [UNCHANGED] 'download_url' MUST NOT be here.
        #    This list is correct.
        read_only_fields = [
            'id',
            'status',
            'task_type',
            'result',
            'created',
            'modified',
        ]

    # [UNCHANGED] This method is correct
    def get_download_url(self, obj: Task):
        if obj.status == Task.TaskStatus.COMPLETED and \
                obj.result and \
                obj.result.get("output_file_path"):

            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(
                    reverse('task_manager:task-download', kwargs={'task_id': obj.id})
                )
        return None

class FileUploadSerializer(serializers.Serializer):
    """
    用于验证文件上传的简单 Serializer。
    """
    # 'file' 是 Edge 客户端在 multipart/form-data 中
    # 必须提供的字段名称。
    file = serializers.FileField(required=True, write_only=True)
