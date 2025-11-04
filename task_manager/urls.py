# task_manager/urls.py
from django.urls import path
from .views import (
    FetchAssignedTasksView,
    TaskCreateView,
    TaskDetailView,
    TaskResultDownloadView
)

app_name = 'task_manager'

urlpatterns = [
    # 边缘实例管理
    path('edge/tasks/fetch/', FetchAssignedTasksView.as_view(), name='edge-fetch-tasks'),

    # 云原生任务管理
    # POST /api/v1/tasks/ -> 创建一个新的云任务
    path('', TaskCreateView.as_view(), name='task-create'),

    # --- 在这里添加 ---
    # GET /api/v1/tasks/{task_id}/ -> 查询特定任务的状态和结果
    # <int:task_id> 会捕获URL中的整数，并将其作为名为 'task_id' 的参数传递给 View
    path('<int:task_id>/', TaskDetailView.as_view(), name='task-detail'),
    path('<int:task_id>/download/', TaskResultDownloadView.as_view(), name='task-download'),
    # --------------------
]