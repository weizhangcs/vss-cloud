# task_manager/tasks.py
import logging
from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
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

    except Exception as e:
        error_str = str(e)
        logger.error(f"Error executing Task {task_id}: {error_str}", exc_info=True)

        # --- [新增] 智能限流重试逻辑 ---
        # 检查是否为第三方 API 的 Rate Limit 错误
        # Google: "429", "ResourceExhausted"
        # Aliyun: "Too Many Requests", "Throttling"
        is_rate_limit = any(k in error_str for k in ["429", "ResourceExhausted", "Too Many Requests", "Throttling"])

        if is_rate_limit:
            # 指数退避策略: 5s, 10s, 20s
            retry_delay = 5 * (2 ** self.request.retries)
            logger.warning(
                f"⚠️ Rate limit detected for Task {task_id}. Retrying in {retry_delay}s... (Attempt {self.request.retries + 1}/3)")

            # 重新将任务放回队列 (保留原队列路由)
            # 注意: 这里会抛出 Retry 异常中断当前执行，不会走到下面的 fail 逻辑
            raise self.retry(exc=e, countdown=retry_delay)

        # --- 常规异常处理: 标记为失败 ---
        try:
            with transaction.atomic():
                # 重新获取任务以避免覆盖
                task = Task.objects.get(pk=task_id)
                # FSM Transition: * -> FAILED
                task.fail(error_message=error_str)
                task.save()
        except Exception as final_error:
            logger.critical(f"CRITICAL: Failed to save failure state for Task {task_id}: {final_error}")

        return f"Task {task_id} failed: {error_str}"