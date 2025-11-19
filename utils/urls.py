# utils/urls.py
from django.urls import path
from .views import FileUploadView, TaskResultDownloadView, GenericFileDownloadView

app_name = 'utils'

urlpatterns = [
    # API 端点： POST /api/v1/files/upload/
    path('upload/', FileUploadView.as_view(), name='file-upload'),
# --- [新增] ---
    # 新的 URL：GET /api/v1/files/tasks/<int:task_id>/download/
    path('tasks/<int:task_id>/download/', TaskResultDownloadView.as_view(), name='task-download'),
    # -----------------

# --- [新增] 通用下载路由 ---
    # API 端点： GET /api/v1/files/download/?path=...
    path('download/', GenericFileDownloadView.as_view(), name='generic-file-download'),
    # ---------------------------
]