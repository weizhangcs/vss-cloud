# task_manager/signals.py
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Task
from .tasks import execute_cloud_native_task # 导入我们刚刚定义的Celery任务

@receiver(post_save, sender=Task)
def trigger_task_execution(sender, instance, created, **kwargs):
    """
    当一个新的 Task 实例的保存事务 *成功提交后*，这个处理器会被调用。
    """
    if created:
        cloud_native_tasks = [
            Task.TaskType.GENERATE_NARRATION,
            Task.TaskType.DEPLOY_RAG_CORPUS,
            Task.TaskType.CHARACTER_IDENTIFIER,
            Task.TaskType.GENERATE_EDITING_SCRIPT,
            Task.TaskType.GENERATE_DUBBING,
            Task.TaskType.LOCALIZE_NARRATION
        ]

        if instance.task_type in cloud_native_tasks:
            # 2. 将 .delay() 调用包裹在 transaction.on_commit 中
            # 这确保了只有在 Task 记录确实已经存在于数据库中之后，
            # Celery 任务才会被发送到队列里。
            print(f"New cloud-native task saved (ID: {instance.id}). Scheduling Celery task on transaction commit...")

            transaction.on_commit(
                lambda: execute_cloud_native_task.delay(instance.id)
            )