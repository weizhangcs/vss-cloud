# utils/views.py
import uuid
from pathlib import Path

from anyio.abc import TaskStatus
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.http import FileResponse, Http404  # <-- [新增]
from rest_framework.generics import get_object_or_404  # <-- [新增]

from task_manager.authentication import EdgeInstanceAuthentication
from .serializers import FileUploadSerializer

# --- [新增] 导入 Task 模型 ---
from task_manager.models import Task
# -----------------------------

import logging # <-- [新增]
logger = logging.getLogger(__name__) # <-- [新增]


class FileUploadView(APIView):
    """
    一个专用的端点，用于将文件上传到共享的 'tmp' 目录。
    """
    authentication_classes = [EdgeInstanceAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = FileUploadSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = serializer.validated_data['file']

        # --- [核心修改] ---
        # 1. 定义存储位置为 SHARED_TMP_ROOT
        fs = FileSystemStorage(location=settings.SHARED_TMP_ROOT)
        # ------------------

        # 2. 创建一个唯一的、安全的文件名
        unique_filename = f"{uuid.uuid4()}_{uploaded_file.name}"

        try:
            # 3. 保存文件到共享卷的 'tmp' 目录下
            saved_name = fs.save(unique_filename, uploaded_file)

            # --- [核心修改] ---
            # 4. 返回相对于 SHARED_ROOT 的 *完整* 相对路径
            # (例如: 'tmp/8b6b787b-..._ep01.srt')
            relative_path_from_root = Path(settings.SHARED_TMP_ROOT.name) / saved_name
            # ------------------

            return Response(
                {"relative_path": str(relative_path_from_root)},  # <-- 修改
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {"error": f"Could not save file: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class TaskResultDownloadView(APIView):
    """
    允许已认证的边缘实例下载已完成任务的结果文件。
    [已从 task_manager.views 移至此处]
    """
    authentication_classes = [EdgeInstanceAuthentication]

    def get(self, request, task_id, *args, **kwargs):
        edge_instance = request.auth

        # 1. 验证任务归属
        queryset = Task.objects.filter(organization=edge_instance.organization)
        task = get_object_or_404(queryset, pk=task_id)

        # 2. 检查任务状态
        if task.status != Task.TaskStatus.COMPLETED:
            return Response(
                {"error": "Task is not yet completed."},
                status=status.HTTP_425_TOO_EARLY
            )

        try:
            # 3. 从 result 字段获取 *相对* 路径
            relative_path_str = task.result.get("output_file_path")
            if not relative_path_str:
                raise ValueError("output_file_path not found in task result.")

            # --- [核心 BUG 修复] ---
            # 4. 将其与 SHARED_ROOT 拼接
            file_path = settings.SHARED_ROOT / relative_path_str
            # -----------------------

            # 5. 验证 *绝对* 路径
            if not file_path.is_file():
                logger.error(f"File not found at path: {file_path}")  # 添加日志
                raise Http404("Result file not found on server.")

            # 6. 提供文件
            response = FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)
            return response

        except (ValueError, TypeError, Http404) as e:
            return Response(
                {"error": f"Could not retrieve file: {e}"},
                status=status.HTTP_404_NOT_FOUND
            )


class GenericFileDownloadView(APIView):
    """
    [新增] 允许已认证的边缘实例下载共享 tmp 目录中的任意文件。

    使用查询参数 ?path=... 来指定文件路径。
    例如: /api/v1/files/download/?path=tmp/dubbing_task_25_audio/narration_000.wav
    """
    authentication_classes = [EdgeInstanceAuthentication]

    def get(self, request, *args, **kwargs):
        # 1. 从查询参数中获取相对路径
        relative_path_str = request.query_params.get('path')
        if not relative_path_str:
            return Response(
                {"error": "Missing 'path' query parameter."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 2. 构建绝对路径
            #    我们只允许从 SHARED_ROOT (即 /app/shared_media) 下载
            file_path = (settings.SHARED_ROOT / relative_path_str).resolve()

            # 3. [安全检查]
            #    确保解析后的路径仍然在 SHARED_ROOT 目录内，
            #    这可以防止路径遍历攻击 (e.g., ?path=../../etc/passwd)
            shared_root_abs = settings.SHARED_ROOT.resolve()
            if shared_root_abs not in file_path.parents:
                return Response(
                    {"error": "Path traversal detected."},
                    status=status.HTTP_403_FORBIDDEN
                )

            # 4. 检查文件是否存在
            if not file_path.is_file():
                raise Http404(f"File not found at: {relative_path_str}")

            # 5. 所有检查通过，提供文件下载
            response = FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)
            return response

        except Http404 as e:
            logger.warning(f"Generic file download failed (Not Found): {e}")
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Generic file download failed (Invalid Path): {e}", exc_info=True)
            return Response({"error": "Invalid path or file error."}, status=status.HTTP_400_BAD_REQUEST)
# --- [新增结束] ---