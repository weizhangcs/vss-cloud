# core/__init__.py

# 这将确保 Celery app 在 Django 启动时总能被导入，
# 这样 @shared_task 装饰器就会使用这个 app。
from .celery import app as celery_app

__all__ = ('celery_app',)