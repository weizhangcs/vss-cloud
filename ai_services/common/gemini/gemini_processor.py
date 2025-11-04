# task_manager/common/gemini/gemini_processor.py

import json
import re
import time
import inspect
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union, List, Callable, Awaitable
import logging

from google import genai
from google.api_core import exceptions
from google.genai.errors import ServerError
from google.genai import types
from django.conf import settings

class GeminiProcessor:
    """
    [已重构] 增强版的Gemini API处理器，适配Django项目。
    配置通过 Django settings 加载，日志记录器通过依赖注入传入。
    """
    _MAX_RETRIES = 3
    _INITIAL_RETRY_DELAY = 1
    _MAX_RETRY_DELAY = 10
    _RETRYABLE_ERRORS = (
        exceptions.ServiceUnavailable, ServerError, exceptions.TooManyRequests,
        exceptions.InternalServerError, exceptions.GatewayTimeout,
    )

    def __init__(self, logger: logging.Logger, debug_dir: Union[str, Path] = "gemini_debug", caller_class: Optional[str] = None):
        """
        初始化时，直接从 Django settings 获取 API 密钥。
        """
        self.api_key = settings.GOOGLE_API_KEY
        self.logger = logger
        self.debug_mode = settings.DEBUG  # 在开发模式下自动开启Debug
        self.debug_dir = Path(debug_dir)
        self.caller_class = caller_class or "default"

        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY 未在Django settings中配置。")

        genai.configure(api_key=self.api_key)
        self.logger.info("GeminiProcessor initialized and configured via Django settings.")

    # ======================================================================
    #  您提供的 Gemini_processor.py 文件中的所有其他方法
    #  (generate_content, _retry_async_operation, _log_to_file, _parse_json_response, 等)
    #  都可以在这里原封不动地复制过来，它们的核心逻辑无需改变。
    # ======================================================================

    def _log_and_raise(self, e: Exception, context: str) -> None:
        """ 辅助函数，用于记录和重新抛出异常 """
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

    def _retry_api_call(self, api_func: Callable, context: str) -> Any:
        """
        同步API调用的重试包装器
        """
        last_exception = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = api_func()
                # <--- 主要修改: 如果在重试后成功，则打印成功信息
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
        异步API调用的重试包装器
        """
        last_exception = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = await api_func_awaitable()
                # <--- 主要修改: 异步方法同样增加成功提示
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

    def generate_content(
            self,
            model_name: str,
            prompt: Union[str, List],
            stream: bool = False,
            temperature: Optional[float] = None,
            **generation_kwargs
    ) -> tuple:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')

        config_params = {
            'temperature': temperature,
            **{k: v for k, v in generation_kwargs.items()
               if k in types.GenerateContentConfig.__annotations__}
        }
        config = types.GenerateContentConfig(**config_params) if any(config_params.values()) else None

        log_kwargs = {k: dict(v) if isinstance(v, types.GenerateContentConfig) else v for k, v in
                      generation_kwargs.items()}

        request_log = {
            "model": model_name, "prompt": prompt, "kwargs": log_kwargs,
            "timestamp": timestamp, "caller": self.caller_class
        }
        self._log_to_file("requests", "request_", request_log)

        start_time = datetime.now()
        try:
            if stream:
                api_call = lambda: self._client.models.generate_content_stream(
                    model=model_name, contents=prompt, config=config
                )
                response_iterator = self._retry_api_call(api_call, "同步流式生成")

                full_response_text = "".join(chunk.text for chunk in response_iterator)
                prompt_tokens = self.count_tokens(prompt, model_name)
                completion_tokens = self.count_tokens(full_response_text, model_name)

                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens
                }
            else:
                api_call = lambda: self._client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                response = self._retry_api_call(api_call, "同步生成")

                full_response_text = response.text
                usage = {
                    "prompt_tokens": response.usage_metadata.prompt_token_count,
                    "completion_tokens": response.usage_metadata.candidates_token_count,
                    "total_tokens": response.usage_metadata.total_token_count
                }

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
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

    async def generate_content_async(
            self,
            model_name: str,
            prompt: Union[str, List],
            **generation_kwargs
    ) -> tuple[Dict[str, Any], Dict[str, int]]:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')

        log_kwargs = {k: dict(v) if isinstance(v, types.GenerateContentConfig) else v for k, v in
                      generation_kwargs.items()}

        request_log = {
            "model": model_name, "prompt": prompt, "kwargs": log_kwargs,
            "timestamp": timestamp, "caller": self.caller_class
        }
        self._log_to_file("requests_async", "request_", request_log)

        try:
            api_call = lambda: self._client.aio.models.generate_content(
                model=model_name, contents=prompt, **generation_kwargs
            )
            response = await self._retry_api_call_async(api_call, "异步生成")

            full_response_text = response.text
            self._log_to_file("raw_responses_async", "raw_", full_response_text)

            usage = {
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

        # ... (您所有其他的方法保持不变)

    def count_tokens(self, contents: Union[str, List], model_name: str) -> int:
        """计算token数量"""
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

    def _get_caller_class_name(self) -> str:
        """自动检测调用者的类名"""
        frame = inspect.currentframe()
        try:
            while frame:
                frame = frame.f_back
                if not frame:
                    break
                if 'self' in frame.f_locals:
                    instance = frame.f_locals['self']
                    if hasattr(instance, '__class__'):
                        return instance.__class__.__name__
            return self.__class__.__name__
        finally:
            del frame

    def _log_to_file(self, subdir: str, prefix: str, content: Any) -> Optional[Path]:
        if not self.debug_mode:
            return None
        log_dir = self.log_dir / subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"{prefix}{timestamp}.json"
        filepath = log_dir / filename
        try:
            sanitized = self._sanitize_content(content)
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
        if isinstance(content, dict):
            content = content.copy()
            for key in list(content.keys()):
                if "key" in key.lower() or "secret" in key.lower():
                    content[key] = "***REDACTED***"
        return content

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
        json_str = match.group(1) if match else text
        json_str_fixed = re.sub(r',\s*([}\]])', r'\1', json_str)
        try:
            return json.loads(json_str_fixed)
        except json.JSONDecodeError:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                self._log_to_file("errors", "parsing_error_", {
                    "error": "Final JSON parsing failed after all fix attempts.",
                    "original_error": str(e),
                    "original_snippet": text[:500],
                    "processed_snippet": json_str_fixed[:500]
                })
                raise ValueError(f"JSON解析失败: {e}\n片段: {json_str[:200]}...")

    def _get_clean_stacktrace(self) -> List[str]:
        stack = []
        for frame_info in inspect.stack():
            if "gemini_processor" in frame_info.filename.lower():
                continue
            stack.append(f"{frame_info.filename}:{frame_info.lineno} ({frame_info.function})")
        return stack
