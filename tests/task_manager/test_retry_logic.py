# tests/task_manager/test_retry_logic.py
import unittest
from unittest.mock import patch, MagicMock
from celery.exceptions import Retry
from django.test import TestCase
from task_manager.models import Task
from task_manager.tasks import execute_cloud_native_task
from organization.models import Organization


class RetryLogicTest(TestCase):
    def setUp(self):
        # [修正] 1. 先创建一个测试用的组织
        self.org = Organization.objects.create(
            name="Test Org",
            attribute=Organization.OrgAttribute.COMPANY
        )

        # [修正] 2. 使用真实的 organization 实例创建任务
        self.task = Task.objects.create(
            task_type=Task.TaskType.GENERATE_NARRATION,
            payload={"test": "data"},
            organization=self.org,  # <--- 使用实例，而非硬编码 ID
            status=Task.TaskStatus.PENDING
        )

    @patch('task_manager.tasks.HandlerRegistry.get_handler')
    def test_rate_limit_retry(self, mock_get_handler):
        """
        测试当 Handler 抛出 429 错误时，Task 是否会触发 Retry
        """
        # 1. 模拟 Handler
        mock_handler_instance = MagicMock()
        # 模拟抛出 Google 风格的限流错误
        mock_handler_instance.handle.side_effect = Exception("429 ResourceExhausted: Quota exceeded")
        mock_get_handler.return_value = mock_handler_instance

        # 2. 模拟 Celery 的 retry 方法
        # 因为 execute_cloud_native_task 被 @shared_task 装饰，我们需要 mock 它的 request 上下文
        # 这里我们直接调用函数的 .apply() 方法在当前线程同步执行，
        # 或者更简单：我们 mock self.retry 来验证它是否被调用

        with patch('task_manager.tasks.execute_cloud_native_task.retry') as mock_retry:
            # 让 retry 抛出 Retry 异常以中断流程（模拟真实 Celery 行为）
            mock_retry.side_effect = Retry()

            # 3. 执行任务
            try:
                execute_cloud_native_task(self.task.id)
            except Retry:
                pass  # 预期内的中断

            # 4. 验证
            # 验证 handler 是否被调用
            mock_handler_instance.handle.assert_called_once()

            # [核心验证] 验证是否触发了 retry，并且检查 countdown 是否符合指数退避
            # 第一次重试，request.retries 默认为 0，所以 delay = 5 * (2^0) = 5
            args, kwargs = mock_retry.call_args
            print(f"Captured Retry Call: {kwargs}")

            self.assertTrue(mock_retry.called)
            # 验证是否捕获到了异常对象
            self.assertIn('exc', kwargs)
            self.assertIn('429', str(kwargs['exc']))
            # 验证倒计时 (根据代码逻辑: 5 * 2^0 = 5s)
            self.assertEqual(kwargs.get('countdown'), 5)

            # 验证数据库状态：任务不应被标记为 FAILED
            self.task.refresh_from_db()
            self.assertNotEqual(self.task.status, Task.TaskStatus.FAILED)
            print("✅ 成功验证：触发了 Celery Retry，并未标记任务失败。")

    @patch('task_manager.tasks.HandlerRegistry.get_handler')
    def test_normal_exception_fail(self, mock_get_handler):
        """
        测试当 Handler 抛出普通错误时，Task 直接失败
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