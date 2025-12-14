from django.urls import path
from .views import FileUploadView, TaskResultDownloadView, GenericFileDownloadView

app_name = 'file_service'

urlpatterns = [
    # POST /api/v1/files/upload/
    path('upload/', FileUploadView.as_view(), name='file-upload'),

    # GET /api/v1/files/tasks/<int:task_id>/download/
    path('tasks/<int:task_id>/download/', TaskResultDownloadView.as_view(), name='task-download'),

    # GET /api/v1/files/download/?path=...
    path('download/', GenericFileDownloadView.as_view(), name='generic-file-download'),
]