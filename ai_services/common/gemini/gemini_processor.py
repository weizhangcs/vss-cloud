# ai_services/common/gemini/gemini_processor.py
# 描述: 增强版的 Gemini API 处理器，用于处理同步/异步请求、重试和 JSON 提取。
# 版本: 1.0 (重构版 - 基于 genai.Client)

import json
import re
import time
import inspect
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union, List, Callable, Awaitable, Tuple
import logging

from google import genai
from google.api_core import exceptions
from google.genai.errors import ServerError
from google.genai import types


class GeminiProcessor:
    """
    [已重构] 增强版的Gemini API处理器。
    负责封装与 Google Gemini API 的所有交互逻辑，包括：
    - 初始化 genai.Client 实例。
    - 统一的错误处理和自动重试机制。
    - 从 Markdown 围栏中安全提取 JSON 响应。
    - 记录请求和响应日志 (调试模式)。
    """

    # --- 静态配置常量 ---
    _MAX_RETRIES = 3
    _INITIAL_RETRY_DELAY = 1
    _MAX_RETRY_DELAY = 10
    # 定义可重试的 API 错误类型 (例如：服务器错误、服务不可用、速率限制)
    _RETRYABLE_ERRORS = (
        exceptions.ServiceUnavailable, ServerError, exceptions.TooManyRequests,
        exceptions.InternalServerError, exceptions.GatewayTimeout,
    )

    def __init__(self, api_key: str, logger: logging.Logger, debug_mode: bool = False, debug_dir: Union[str, Path] = "gemini_debug", caller_class: Optional[str] = None):
        """
        初始化时，接收所有必要的配置作为参数，并创建客户端实例。

        Args:
            api_key (str): Google Gemini API Key。
            logger (logging.Logger): 日志记录器实例 (通过依赖注入传入)。
            debug_mode (bool): 是否开启调试模式，保存请求/响应日志。
            debug_dir (Union[str, Path]): 调试日志的保存目录。
            caller_class (Optional[str]): 调用该处理器的服务类名。
        """
        if not api_key:
            raise ValueError("API Key 不能为空。")

        self.api_key = api_key
        self.logger = logger
        self.debug_mode = debug_mode
        self.debug_dir = Path(debug_dir)

        # [修改] 2. 增加路径自动收敛逻辑
        if self.debug_mode:
            if debug_dir:
                self.debug_dir = Path(debug_dir)
            else:
                # 如果开启调试但未指定路径，强制收敛到 shared_media/logs/gemini_debug
                # 注意：这里假设运行目录是项目根目录，或者通过相对路径访问
                self.debug_dir = Path("shared_media/logs/gemini_debug")
                # 可选：打印一条警告日志，提示使用了默认路径
                # self.logger.warning(f"Debug mode on but no path provided. Using default: {self.debug_dir}")

            self.log_dir = self.debug_dir
        else:
            self.debug_dir = None
            self.log_dir = None

        self.caller_class = caller_class or self._get_caller_class_name()
        # 创建一个基于调用者和时间的会话ID，用于日志文件名
        self.session_id = f"{self.caller_class}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 核心：使用 genai.Client 实例化，负责管理连接
        try:
            self._client = genai.Client(api_key=self.api_key)
            self.logger.info("GeminiProcessor initialized and genai.Client created successfully.")
        except Exception as e:
            self.logger.error(f"初始化 genai.Client 时失败: {e}", exc_info=True)
            raise

    def generate_content(
            self,
            model_name: str,
            prompt: Union[str, List],
            stream: bool = False,
            temperature: Optional[float] = None,
            **generation_kwargs
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        执行同步的 AI 内容生成请求。

        Args:
            model_name (str): 要使用的 Gemini 模型名称。
            prompt (Union[str, List]): 输入的 Prompt 文本或多部分内容列表。
            stream (bool): 是否使用流式响应 (当前同步模式下默认不使用)。
            temperature (Optional[float]): 模型生成温度。
            **generation_kwargs: 额外的生成配置参数。

        Returns:
            tuple: (解析后的 JSON 数据, AI 调用用量报告)。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')

        # 构建配置对象
        config_params = {'temperature': temperature}
        config = types.GenerateContentConfig(**config_params) if any(config_params.values()) else None

        request_log = {
            "model": model_name, "prompt": prompt, "kwargs": generation_kwargs,
            "timestamp": timestamp, "caller": self.caller_class
        }
        self._log_to_file("requests", "request_", request_log)

        start_time = datetime.now()
        try:
            # 定义 API 调用的函数句柄，传递给重试包装器
            api_call = lambda: self._client.models.generate_content(
                model=model_name, contents=prompt, config=config
            )
            response = self._retry_api_call(api_call, "同步生成")

            full_response_text = response.text

            # 提取 Tokens 用量
            usage = {
                "model_used": model_name,
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count
            }

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            # 丰富用量报告，包含时间戳和请求计数
            usage.update({
                "start_time_utc": start_time.isoformat(),
                "end_time_utc": end_time.isoformat(),
                "duration_seconds": round(duration, 4),
                "request_count": 1
            })

            self._log_to_file("raw_responses", "raw_", full_response_text)
            parsed_data = self._parse_json_response(full_response_text)
            self._log_to_file("parsed_responses", "parsed_", {
                "data": parsed_data, "usage": usage, "timestamp": timestamp
            })

            return parsed_data, usage

        except Exception as e:
            self._log_and_raise(e, "生成内容")

    def count_tokens(self, contents: Union[str, List], model_name: str) -> int:
        """计算给定内容在特定模型下的 token 数量。"""
        try:
            response = self._client.models.count_tokens(model=model_name, contents=contents)
            return response.total_tokens
        except Exception as e:
            self._log_to_file("errors", "token_count_error_", {
                "error": str(e),
                "contents": contents[:200] if isinstance(contents, str) else contents,
                "model": model_name
            })
            raise RuntimeError(f"Token计数失败: {str(e)}") from e

    def _retry_api_call(self, api_func: Callable, context: str) -> Any:
        """
        同步 API 调用的重试包装器，实现指数退避和错误捕获。
        """
        last_exception = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = api_func()
                # 重试成功后打印提示信息
                if attempt > 0:
                    print(f"✅ API调用重试成功 (在第 {attempt + 1} 次尝试)。继续执行...")
                return result
            except self._RETRYABLE_ERRORS as e:
                last_exception = e
                if attempt < self._MAX_RETRIES:
                    delay = min(self._INITIAL_RETRY_DELAY * (2 ** attempt), self._MAX_RETRY_DELAY)
                    print(
                        f"API调用失败 ({type(e).__name__})，将在 {delay} 秒后重试... (尝试 {attempt + 1}/{self._MAX_RETRIES})")
                    time.sleep(delay)
                    continue
                else:
                    print(f"API调用在 {self._MAX_RETRIES} 次重试后彻底失败。")
                    self._log_and_raise(e, f"{context} (重试 {self._MAX_RETRIES} 次后)")
        raise last_exception

    async def _retry_api_call_async(self, api_func_awaitable: Callable[[], Awaitable], context: str) -> Any:
        """
        异步 API 调用的重试包装器 (与同步版本逻辑类似)。
        """
        last_exception = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = await api_func_awaitable()
                # 异步方法同样增加成功提示
                if attempt > 0:
                    print(f"✅ 异步API调用重试成功 (在第 {attempt + 1} 次尝试)。继续执行...")
                return result
            except self._RETRYABLE_ERRORS as e:
                last_exception = e
                if attempt < self._MAX_RETRIES:
                    delay = min(self._INITIAL_RETRY_DELAY * (2 ** attempt), self._MAX_RETRY_DELAY)
                    print(
                        f"异步API调用失败 ({type(e).__name__})，将在 {delay} 秒后重试... (尝试 {attempt + 1}/{self._MAX_RETRIES})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"异步API调用在 {self._MAX_RETRIES} 次重试后彻底失败。")
                    self._log_and_raise(e, f"{context} (重试 {self._MAX_RETRIES} 次后)")
        raise last_exception

    async def generate_content_async(
            self,
            model_name: str,
            prompt: Union[str, List],
            **generation_kwargs
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        执行异步的 AI 内容生成请求。

        【预留目的说明】:
        此方法用于 Django ASGI 或 asyncio 并发场景。
        在 Gemini Developer API 的速率限制约束下，同步方法 (`generate_content`) 配合内置重试已足够高效。
        本方法预留给未来需要**并行批处理**或**高并发 API 视图**时使用。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')

        # 准备日志记录的参数 (处理 config object 等)
        log_kwargs = {k: dict(v) if isinstance(v, types.GenerateContentConfig) else v for k, v in
                      generation_kwargs.items()}

        request_log = {
            "model": model_name, "prompt": prompt, "kwargs": log_kwargs,
            "timestamp": timestamp, "caller": self.caller_class
        }
        self._log_to_file("requests_async", "request_", request_log)

        try:
            # 定义异步 API 调用的函数句柄
            api_call = lambda: self._client.aio.models.generate_content(
                model=model_name, contents=prompt, **generation_kwargs
            )
            response = await self._retry_api_call_async(api_call, "异步生成")

            full_response_text = response.text
            self._log_to_file("raw_responses_async", "raw_", full_response_text)

            # 提取 Tokens 用量
            usage = {
                "model_used": model_name,
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }

            parsed_data = self._parse_json_response(full_response_text)
            self._log_to_file("parsed_responses_async", "parsed_", {
                "data": parsed_data, "usage": usage, "timestamp": timestamp
            })

            return parsed_data, usage

        except Exception as e:
            self._log_and_raise(e, "异步生成")

    def _get_caller_class_name(self) -> str:
        """通过检查调用栈，自动检测调用该处理器的上层类名，用于日志记录。"""
        frame = inspect.currentframe()
        try:
            # 遍历调用栈，直到找到包含 'self' 实例的帧
            while frame:
                frame = frame.f_back
                if not frame:
                    break
                if 'self' in frame.f_locals:
                    instance = frame.f_locals['self']
                    if hasattr(instance, '__class__'):
                        return instance.__class__.__name__
            return self.__class__.__name__  # 如果找不到，返回当前类名
        finally:
            del frame

    def _log_and_raise(self, e: Exception, context: str) -> None:
        """
        辅助函数：记录错误日志，并重新抛出异常，附带上下文信息。
        用于处理 API 调用失败或重试耗尽后的最终异常。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        error_info = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "timestamp": timestamp,
            "context": context,
            "stack_trace": self._get_clean_stacktrace()
        }
        log_subdir = "errors_async" if "async" in context else "errors"
        self._log_to_file(log_subdir, "error_", error_info)
        raise RuntimeError(f"{context}失败: {str(e)}") from e

    def _log_to_file(self, subdir: str, prefix: str, content: Any) -> Optional[Path]:
        """将请求/响应数据或错误信息写入调试文件 (如果 debug_mode 开启)。"""
        if not self.debug_mode or not self.log_dir:
            return None

        log_dir = self.log_dir / subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"{prefix}{timestamp}.json"
        filepath = log_dir / filename

        try:
            sanitized = self._sanitize_content(content)  # 移除敏感信息
            with open(filepath, "w", encoding="utf-8") as f:
                if isinstance(sanitized, (dict, list)):
                    json.dump(sanitized, f, indent=2, ensure_ascii=False)
                else:
                    f.write(str(sanitized))
            return filepath
        except Exception as e:
            print(f"⚠️ 日志记录失败({filepath}): {str(e)}")
            return None

    def _sanitize_content(self, content: Any) -> Any:
        """从内容中移除 API key 或 secret 等敏感信息。"""
        if isinstance(content, dict):
            content = content.copy()
            for key in list(content.keys()):
                if "key" in key.lower() or "secret" in key.lower():
                    content[key] = "***REDACTED***"
        return content

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """
        从 LLM 返回的文本中安全地提取和解析 JSON 对象。

        它首先尝试匹配 Markdown JSON 围栏中的内容，并尝试修复常见的尾随逗号错误。
        """
        # 1. 尝试匹配 Markdown JSON 围栏 (例如: ```json{...}```)
        match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
        json_str = match.group(1) if match else text

        # 2. 尝试修复尾随逗号 (例如: {"a": 1,})
        json_str_fixed = re.sub(r',\s*([}\]])', r'\1', json_str)

        try:
            # 3. 尝试解析修复后的字符串
            return json.loads(json_str_fixed)
        except json.JSONDecodeError:
            try:
                # 4. 如果失败，尝试解析原始提取的字符串 (可能修复不需要)
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                # 5. 最终失败，记录并抛出异常
                self._log_to_file("errors", "parsing_error_", {
                    "error": "Final JSON parsing failed after all fix attempts.",
                    "original_error": str(e),
                    "original_snippet": text[:500],
                    "processed_snippet": json_str_fixed[:500]
                })
                raise ValueError(f"JSON解析失败: {e}\n片段: {json_str[:200]}...")

    def _get_clean_stacktrace(self) -> List[str]:
        """获取并清理调用栈信息，排除处理器本身的内部帧，以提供更清晰的错误溯源。"""
        stack = []
        for frame_info in inspect.stack():
            # 排除与当前文件相关的内部调用
            if "gemini_processor" in frame_info.filename.lower():
                continue
            stack.append(f"{frame_info.filename}:{frame_info.lineno} ({frame_info.function})")
        return stack