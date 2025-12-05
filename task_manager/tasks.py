# task_manager/tasks.py
import logging
from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction

from core.exceptions import RateLimitException
from .models import Task
from .handlers import HandlerRegistry

# 获取 Celery 专用的 logger
logger = get_task_logger(__name__)


# [核心修改] 开启 bind=True 以获取 self (用于重试), 设置 max_retries
@shared_task(bind=True, max_retries=3)
def execute_cloud_native_task(self, task_id):
    """
    [重构版] 通用云端任务执行入口。
    v1.2.0-alpha.4: 增加队列感知与限流重试机制。
    """
    logger.info(f"Celery worker received task ID: {task_id}")

    try:
        # --- 阶段 1: 锁定任务并标记为运行中 (短事务) ---
        with transaction.atomic():
            # 使用 select_for_update 锁定行，防止竞态条件
            try:
                task = Task.objects.select_for_update().get(pk=task_id)
            except Task.DoesNotExist:
                logger.error(f"Task {task_id} not found.")
                return f"Task {task_id} not found"

            # 幂等性检查：如果任务已经完成或失败，不要重跑
            if task.status in [Task.TaskStatus.COMPLETED, Task.TaskStatus.FAILED]:
                logger.warning(f"Task {task_id} is already in {task.status} state. Skipping.")
                return f"Task {task_id} already processed"

            # FSM Transition: PENDING -> RUNNING
            task.start()
            task.save()

        logger.info(f"Task {task_id} state transitioned to RUNNING. Handing over to strategy...")

        # --- 阶段 2: 执行具体业务逻辑 (长耗时，无数据库锁) ---
        # 根据任务类型获取对应的 Handler 策略类
        handler = HandlerRegistry.get_handler(task.task_type)

        # 执行业务逻辑
        result_data = handler.handle(task)

        # --- 阶段 3: 标记完成并保存结果 (短事务) ---
        with transaction.atomic():
            # 重新获取最新的任务对象
            task = Task.objects.select_for_update().get(pk=task_id)

            # FSM Transition: RUNNING -> COMPLETED
            task.complete(result_data=result_data)
            task.save()

        logger.info(f"Task {task_id} ({task.task_type}) completed successfully.")
        return f"Task {task_id} success"
    except RateLimitException as e:
        retry_delay = 5 * (2 ** self.request.retries)
        logger.warning(f"⚠️ Rate limit hit: {e.detail}. Retrying in {retry_delay}s...")

        # 抛出 Retry，Celery 会接管
        raise self.retry(exc=e, countdown=retry_delay)
    except Exception as e:
        # 常规异常处理 (Failed)
        error_str = str(e)
        logger.error(f"Error executing Task {task_id}: {error_str}", exc_info=True)

        try:
            with transaction.atomic():
                task = Task.objects.get(pk=task_id)
                task.fail(error_message=error_str)
                task.save()
        except Exception:
            pass

        return f"Task {task_id} failed: {error_str}"