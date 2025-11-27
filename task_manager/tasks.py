# task_manager/tasks.py
import logging
from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
from .models import Task
from .handlers import HandlerRegistry

# 获取 Celery 专用的 logger
logger = get_task_logger(__name__)


@shared_task
def execute_cloud_native_task(task_id):
    """
    [重构版] 通用云端任务执行入口。
    基于策略模式 (HandlerRegistry) 分发任务，并利用 FSM 管理状态流转。
    """
    logger.info(f"Celery worker received task ID: {task_id}")

    try:
        # --- 阶段 1: 锁定任务并标记为运行中 (短事务) ---
        with transaction.atomic():
            # 使用 select_for_update 锁定行，防止竞态条件
            task = Task.objects.select_for_update().get(pk=task_id)

            # FSM Transition: PENDING -> RUNNING
            # 注意: start() 方法内部会自动更新 started_at 字段
            task.start()
            task.save()

        logger.info(f"Task {task_id} state transitioned to RUNNING. Handing over to strategy...")

        # --- 阶段 2: 执行具体业务逻辑 (长耗时，无数据库锁) ---
        # 根据任务类型获取对应的 Handler 策略类
        handler = HandlerRegistry.get_handler(task.task_type)

        # 执行业务逻辑 (可能会耗时数分钟)
        # 结果将是一个字典，或者抛出异常
        result_data = handler.handle(task)

        # --- 阶段 3: 标记完成并保存结果 (短事务) ---
        with transaction.atomic():
            # 重新获取最新的任务对象（因为在执行期间它可能被修改，虽然几率很低）
            task = Task.objects.select_for_update().get(pk=task_id)

            # FSM Transition: RUNNING -> COMPLETED
            # 注意: complete() 方法内部会自动更新 result, finished_at, duration
            task.complete(result_data=result_data)
            task.save()

        logger.info(f"Task {task_id} ({task.task_type}) completed successfully.")
        return f"Task {task_id} success"

    except Exception as e:
        logger.error(f"Error executing Task {task_id}: {e}", exc_info=True)

        # --- 异常处理: 标记为失败 ---
        try:
            with transaction.atomic():
                task = Task.objects.get(pk=task_id)
                # FSM Transition: * -> FAILED
                # 注意: fail() 方法内部会自动记录 error_message 和 finished_at
                task.fail(error_message=str(e))
                task.save()
        except Exception as final_error:
            # 如果连保存失败状态都报错了（比如数据库断连），这是最后的防线
            logger.critical(f"CRITICAL: Failed to save failure state for Task {task_id}: {final_error}")

        return f"Task {task_id} failed: {e}"