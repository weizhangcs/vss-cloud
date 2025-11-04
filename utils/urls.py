# utils/urls.py
from django.urls import path
from task_manager.views import FileUploadView

app_name = 'utils'

urlpatterns = [
    # API 端点： POST /api/v1/files/upload/
    path('upload/', FileUploadView.as_view(), name='file-upload'),
]