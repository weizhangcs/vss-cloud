# core/celery.py
import os
from celery import Celery

# 设置默认的 Django settings 模块，为 'celery' 程序做准备。
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# 创建一个 Celery 应用实例
app = Celery('core')

# 使用一个字符串，这样 worker 就不需要为了配置对象而序列化子进程了。
# - namespace='CELERY' 意味着所有 Celery 相关的配置键都应该有一个 `CELERY_` 的前缀。
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动从所有已注册的 Django app 中加载 tasks.py 文件。
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')