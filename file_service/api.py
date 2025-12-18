import uuid
import logging
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.core.files.storage import FileSystemStorage
from django.shortcuts import get_object_or_404

from ninja import Router, File
from ninja.files import UploadedFile

from core.exceptions import BizException
from core.error_codes import ErrorCode
from task_manager.models import Task

from core.auth import EdgeAuth

# 引入 Pydantic Schema
from .schemas import FileUploadResponse

logger = logging.getLogger(__name__)

# 定义 Router (通常在主 api.py 中挂载时会指定 tags=['Files'])
# 如果需要鉴权: router = Router(auth=EdgeTokenAuth())
router = Router(auth=EdgeAuth())


@router.post("/upload", response=FileUploadResponse, summary="上传文件")
def upload_file(request, file: UploadedFile = File(...)):
    """
    [Infrastructure] 通用文件上传接口。
    - 接收: multipart/form-data ('file')
    - 返回: 相对路径
    """
    # 1. 校验 (Ninja 已自动校验 file 是否存在)

    # 2. 存储逻辑
    fs = FileSystemStorage(location=settings.SHARED_TMP_ROOT)
    # 使用 UUID 避免文件名冲突
    ext = Path(file.name).suffix
    unique_filename = f"{uuid.uuid4()}{ext}"

    try:
        saved_name = fs.save(unique_filename, file)
        # 计算相对于 SHARED_ROOT 的路径 (假设 SHARED_TMP_ROOT 在 SHARED_ROOT 之下)
        # 如果 SHARED_TMP_ROOT 是独立目录，这里返回相对路径即可
        # 这里为了稳健，我们返回相对于 SHARED_TMP_ROOT 的文件名，或者根据业务约定返回完整相对路径

        # 假设业务方需要的是基于 settings.SHARED_ROOT 的相对路径
        # 且 settings.SHARED_TMP_ROOT 是 settings.SHARED_ROOT 的子目录 (e.g., 'tmp')

        abs_path = Path(fs.path(saved_name))
        try:
            relative_path = abs_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            # 如果不在 SHARED_ROOT 下，则返回相对于 TMP 的路径 (根据实际部署调整)
            relative_path = Path(settings.SHARED_TMP_ROOT.name) / saved_name

        logger.info(f"File uploaded successfully: {relative_path}")

        return FileUploadResponse(
            relative_path=str(relative_path)
        )

    except Exception as e:
        logger.error(f"File save failed: {e}")
        raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Could not save file: {e}")


@router.get("/tasks/{task_id}/download", url_name="task_download", summary="下载任务结果")
def download_task_result(request, task_id: int):
    """
    [Infrastructure] 任务结果下载接口。
    """
    # 1. 获取任务 & 权限校验
    # 注意: 如果 Router 配置了 Auth，request.auth / request.edge_instance 应该已有值
    # 这里保持简单的逻辑

    # edge_instance = request.auth
    # queryset = Task.objects.filter(organization=edge_instance.organization)
    # task = get_object_or_404(queryset, pk=task_id)

    # [简化版] 直接查 Task (假设鉴权已在 Router 层处理)
    task = get_object_or_404(Task, pk=task_id)

    # 2. 状态校验
    if task.status != Task.TaskStatus.COMPLETED:
        # HTTP 425 Too Early 语义上适合“任务未完成”
        raise BizException(ErrorCode.TASK_EXECUTION_FAILED,
                           msg="Task is not yet completed.",
                           status_code=425)

    try:
        # 3. 获取路径
        # 假设 result 结构: {"output_file_path": "localization/123_en.json", ...}
        if not task.result:
            raise BizException(ErrorCode.FILE_IO_ERROR, msg="Task result is empty.")

        relative_path_str = task.result.get("output_file_path")
        if not relative_path_str:
            raise ValueError("output_file_path missing in result.")

        file_path = settings.SHARED_ROOT / relative_path_str

        # 4. 文件存在性校验
        if not file_path.is_file():
            logger.error(f"Result file missing on disk: {file_path}")
            raise Http404("Result file missing on disk.")

        # 5. 返回文件流
        return FileResponse(file_path.open('rb'), as_attachment=True, filename=file_path.name)

    except Http404:
        raise
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise BizException(ErrorCode.FILE_IO_ERROR, msg=str(e))


@router.get("/download", summary="通用文件下载")
def download_generic_file(request, path: str):
    """
    [Infrastructure] 通用文件下载接口。
    - 参数: path (相对路径)
    """
    if not path:
        raise BizException(ErrorCode.INVALID_PARAM, msg="Missing 'path' query parameter.")

    try:
        # 1. 路径解析与安全防御
        target_path = (settings.SHARED_ROOT / path).resolve()
        root_path = settings.SHARED_ROOT.resolve()

        # Path Traversal Check (必须在 Root 目录下)
        if root_path not in target_path.parents and root_path != target_path.parent:
            # 严格模式：只能下载 Shared Root 下的文件
            logger.warning(f"Path traversal attempt: {path}")
            raise BizException(ErrorCode.PERMISSION_DENIED, msg="Access denied.", status_code=403)

        if not target_path.is_file():
            raise Http404(f"File not found: {path}")

        return FileResponse(target_path.open('rb'), as_attachment=True, filename=target_path.name)

    except Http404:
        raise
    except Exception as e:
        if isinstance(e, BizException): raise e
        logger.error(f"Generic download failed: {e}")
        raise BizException(ErrorCode.FILE_IO_ERROR, msg=f"Download failed: {e}")