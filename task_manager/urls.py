# task_manager/urls.py
from django.urls import path
from .views import (
    # FetchAssignedTasksView, # 已删除
    TaskCreateView,
    TaskDetailView,
)

app_name = 'task_manager'

urlpatterns = [
    # [修改] 移除了 edge/tasks/fetch/ 路由

    # 云原生任务管理
    path('', TaskCreateView.as_view(), name='task-create'),
    path('<int:task_id>/', TaskDetailView.as_view(), name='task-detail'),
]