# tests/task_manager/test_retry_logic.py
import unittest
from unittest.mock import patch, MagicMock
from celery.exceptions import Retry
from django.test import TestCase
from task_manager.models import Task
from task_manager.tasks import execute_cloud_native_task
from organization.models import Organization
# [新增] 引入我们定义的新异常
from core.exceptions import RateLimitException


class RetryLogicTest(TestCase):
    def setUp(self):
        # 1. 创建组织
        self.org = Organization.objects.create(
            name="Test Org",
            attribute=Organization.OrgAttribute.COMPANY
        )

        # 2. 创建任务
        self.task = Task.objects.create(
            task_type=Task.TaskType.GENERATE_NARRATION,
            payload={"test": "data"},
            organization=self.org,
            status=Task.TaskStatus.PENDING
        )

    @patch('task_manager.tasks.HandlerRegistry.get_handler')
    def test_rate_limit_retry(self, mock_get_handler):
        """
        [修改] 测试当 Handler 抛出 RateLimitException 时，Task 是否会触发 Retry
        """
        # 1. 模拟 Handler
        mock_handler_instance = MagicMock()

        # [核心修改] 让 Mock 抛出强类型的 RateLimitException
        # 而不是之前的 Exception("429...")
        mock_handler_instance.handle.side_effect = RateLimitException(
            msg="Quota exceeded test",
            provider="GoogleMock"
        )

        mock_get_handler.return_value = mock_handler_instance

        # 2. 模拟 Celery 的 retry
        with patch('task_manager.tasks.execute_cloud_native_task.retry') as mock_retry:
            # 让 retry 抛出 Retry 异常以中断流程
            mock_retry.side_effect = Retry()

            # 3. 执行任务
            try:
                execute_cloud_native_task(self.task.id)
            except Retry:
                pass

                # 4. 验证
            args, kwargs = mock_retry.call_args

            self.assertTrue(mock_retry.called)

            # 验证捕获到的异常类型是否正确
            exc = kwargs.get('exc')
            self.assertIsInstance(exc, RateLimitException)  # 确保它是我们定义的类型

            # 验证倒计时 (5 * 2^0 = 5s)
            self.assertEqual(kwargs.get('countdown'), 5)

            # 验证数据库状态：任务不应被标记为 FAILED
            self.task.refresh_from_db()
            self.assertNotEqual(self.task.status, Task.TaskStatus.FAILED)
            print("✅ 成功验证：RateLimitException 正确触发了 Celery Retry。")

    @patch('task_manager.tasks.HandlerRegistry.get_handler')
    def test_normal_exception_fail(self, mock_get_handler):
        """
        测试当 Handler 抛出普通错误时，Task 直接失败 (保持不变)
        """
        mock_handler = MagicMock()
        mock_handler.handle.side_effect = Exception("ValueError: Invalid JSON")
        mock_get_handler.return_value = mock_handler

        # 执行
        execute_cloud_native_task(self.task.id)

        # 验证
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.TaskStatus.FAILED)
        print("✅ 成功验证：普通异常直接标记任务失败。")