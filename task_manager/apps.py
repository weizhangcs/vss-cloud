from django.apps import AppConfig


class TaskManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'task_manager'

    def ready(self):
        # 导入并注册信号处理器
        import task_manager.signals