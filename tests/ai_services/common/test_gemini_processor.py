import unittest
from unittest.mock import patch, Mock, MagicMock
from pathlib import Path
import json
import logging
import shutil
from google.api_core import exceptions

# 导入目标类
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor

# 创建一个 Mock Logger
mock_logger = MagicMock(spec=logging.Logger)


# --- 辅助类：模拟 Gemini API 响应 ---
class MockResponse:
    """模拟 genai.Client.models.generate_content 返回的 Response 对象"""

    def __init__(self, text, prompt_tokens, completion_tokens):
        self.text = text

        # 模拟 usage_metadata 属性
        mock_usage_metadata = Mock()
        mock_usage_metadata.prompt_token_count = prompt_tokens
        mock_usage_metadata.candidates_token_count = completion_tokens
        mock_usage_metadata.total_token_count = prompt_tokens + completion_tokens

        self.usage_metadata = mock_usage_metadata


# --- 测试类 ---
class GeminiProcessorTests(unittest.TestCase):
    """
    针对 ai_services.common.gemini.gemini_processor.py 的单元测试。
    """

    def setUp(self):
        # 创建一个临时目录用于调试日志（因为代码会尝试写入）
        self.debug_dir = Path("mock_gemini_debug")
        self.debug_dir.mkdir(exist_ok=True)
        self.mock_api_key = "MOCK_API_KEY_123"

    def tearDown(self):
        # 清理临时目录
        shutil.rmtree(self.debug_dir, ignore_errors=True)

    def test_01_initialization_no_key(self):
        """测试没有 API Key 时是否抛出 ValueError"""
        with self.assertRaises(ValueError):
            GeminiProcessor(api_key="", logger=mock_logger)

    @patch('ai_services.common.gemini.gemini_processor.genai.Client')
    def test_02_generate_content_success(self, MockGenaiClient):
        """测试成功的 API 调用，验证返回的数据结构和用量提取"""

        # 1. 配置 Mock 响应
        expected_json = {"result": "The final story is great."}
        mock_text = f"Here is the result:\n```json\n{json.dumps(expected_json)}\n```"
        mock_response = MockResponse(
            text=mock_text,
            prompt_tokens=100,
            completion_tokens=50
        )

        # 2. 配置 Mock Client 的行为:
        MockGenaiClient.return_value.models.generate_content.return_value = mock_response

        # 3. 实例化并执行
        processor = GeminiProcessor(
            api_key=self.mock_api_key,
            logger=mock_logger,
            debug_mode=False
        )
        parsed_data, usage = processor.generate_content(
            model_name="gemini-2.5-flash",
            prompt="Tell me a story",
            temperature=0.7
        )

        # 4. 验证结果
        self.assertEqual(parsed_data, expected_json)
        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["completion_tokens"], 50)
        self.assertEqual(usage["total_tokens"], 150)
        self.assertIn("duration_seconds", usage)

    @patch('ai_services.common.gemini.gemini_processor.genai.Client')
    def test_03_json_parsing_fix_trailing_comma(self, MockGenaiClient):
        """测试 JSON 解析器是否能修复尾随逗号"""

        # 模拟一个带有多余逗号和 Markdown 围栏的响应
        malformed_json = '{\n"key1": "value1",\n"key2": 123,\n}'  # 尾随逗号
        mock_text = f"```json\n{malformed_json}\n```"
        expected_parsed_data = {"key1": "value1", "key2": 123}

        # 模拟初始化成功
        processor = GeminiProcessor(self.mock_api_key, mock_logger)

        # 手动调用内部的私有方法进行测试
        parsed_data = processor._parse_json_response(mock_text)

        self.assertEqual(parsed_data, expected_parsed_data)

    @patch('ai_services.common.gemini.gemini_processor.genai.Client')
    def test_04_retry_on_service_unavailable(self, MockGenaiClient):
        """测试 API 在遇到可重试错误时是否执行重试逻辑"""

        # 1. 配置 Mock 响应序列
        mock_success_response = MockResponse('```json\n{"status": "ok"}\n```', 1, 1)

        # 2. 配置 Mock Client 的行为:
        mock_method = MockGenaiClient.return_value.models.generate_content

        # Side effect: 第一次调用抛出异常, 第二次调用返回成功
        mock_method.side_effect = [
            exceptions.TooManyRequests("Rate limit exceeded"),
            mock_success_response
        ]

        # 3. 实例化并执行 (重试逻辑会使用 time.sleep)
        processor = GeminiProcessor(
            api_key=self.mock_api_key,
            logger=mock_logger,
            debug_mode=True,
            debug_dir=self.debug_dir
        )

        # 4. 确保重试时使用的 sleep 被 Mock，以避免测试等待（这是关键！）
        with patch('time.sleep', return_value=None):
            parsed_data, usage = processor.generate_content(
                model_name="gemini-2.5-flash",
                prompt="Retry this",
                temperature=0.1
            )

        # 5. 验证结果和调用次数
        self.assertEqual(parsed_data, {"status": "ok"})
        # 验证 generate_content 被调用了两次（一次失败，一次成功）
        self.assertEqual(mock_method.call_count, 2)
        # 验证 usage 中的 request_count 是 1 (代表成功完成了一次逻辑上的请求)
        self.assertEqual(usage['request_count'], 1)


# 如果你需要在本地运行，可以添加以下代码块
if __name__ == '__main__':
    # 注意：在 Django 环境中，请使用 manage.py test
    unittest.main()