# utils/views.py
import uuid
import logging
from pathlib import Path

from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.generics import get_object_or_404

from task_manager.authentication import EdgeInstanceAuthentication
from core.permissions import IsEdgeAuthenticated
# [新增] 引入异常
from core.exceptions import BizException
from core.error_codes import ErrorCode

from task_manager.models import Task
from .serializers import FileUploadSerializer

logger = logging.getLogger(__name__)


class FileUploadView(APIView):
    authentication_classes = [EdgeInstanceAuthentication]
    permission_classes = [IsEdgeAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = FileUploadSerializer(data=request.data)
        if not serializer.is_valid():
            # [优化] 让 DRF 错误通过全局 Handler 渲染
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = serializer.validated_data['file']
        fs = FileSystemStorage(location=settings.SHARED_TMP_ROOT)
        unique_filename = f"{uuid.uuid4()}_{uploaded_file.name}"

        try:
            saved_name = fs.save(unique_filename, uploaded_file)
            relative_path_from_root = Path(settings.SHARED_TMP_ROOT.name) / saved_name
            return Response(
                {"relative_path": str(relative_path_from_root)},
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            # [优化] 抛出业务异常
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Could not save file: {e}")


class TaskResultDownloadView(APIView):
    authentication_classes = [EdgeInstanceAuthentication]
    # [核心修改] 添加权限
    permission_classes = [IsEdgeAuthenticated]

    def get(self, request, task_id, *args, **kwargs):
        edge_instance = request.auth
        queryset = Task.objects.filter(organization=edge_instance.organization)
        task = get_object_or_404(queryset, pk=task_id)

        if task.status != Task.TaskStatus.COMPLETED:
            # [优化] 抛出业务异常 (Task not ready)
            # 使用 HTTP 425 Too Early 或 400
            raise BizException(ErrorCode.TASK_EXECUTION_FAILED, msg="Task is not yet completed.",
                               status_code=status.HTTP_425_TOO_EARLY)

        try:
            relative_path_str = task.result.get("output_file_path")
            if not relative_path_str:
                raise ValueError("output_file_path missing in result.")

            file_path = settings.SHARED_ROOT / relative_path_str

            if not file_path.is_file():
                logger.error(f"File not found: {file_path}")
                raise Http404("Result file missing on disk.")

            return FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)

        except Http404:
            raise  # 让全局处理 404
        except Exception as e:
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=str(e))


class GenericFileDownloadView(APIView):
    authentication_classes = [EdgeInstanceAuthentication]
    # [核心修改] 添加权限
    permission_classes = [IsEdgeAuthenticated]

    def get(self, request, *args, **kwargs):
        relative_path_str = request.query_params.get('path')
        if not relative_path_str:
            raise BizException(ErrorCode.INVALID_PARAM, msg="Missing 'path' query parameter.")

        try:
            file_path = (settings.SHARED_ROOT / relative_path_str).resolve()
            shared_root_abs = settings.SHARED_ROOT.resolve()

            if shared_root_abs not in file_path.parents:
                raise BizException(ErrorCode.PERMISSION_DENIED, msg="Path traversal detected.", status_code=403)

            if not file_path.is_file():
                raise Http404(f"File not found: {relative_path_str}")

            return FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)

        except Http404:
            raise
        except Exception as e:
            # 捕获其他路径解析错误
            if isinstance(e, BizException): raise e
            raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Download failed: {e}")