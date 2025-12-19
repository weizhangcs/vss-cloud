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

from core.exceptions import RateLimitException

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

    def _build_generation_config(self, model_name: str, temperature: Optional[float] = None,
                                 tools: Optional[List] = None) -> Optional[types.GenerateContentConfig]:
        """
        [Final Strategic Fix] 移除 JSON Mode 硬约束，解除与 AFC 的死锁
        """

        # 1. 基础配置：不再强制 response_mime_type="application/json"
        # 我们依靠 Prompt 和 Regex Parser 来保证 JSON 格式
        config_params = {}

        # 2. 安全设置 (保留 BLOCK_NONE，这对 Batch 5 很重要)
        # 使用原生字典列表
        safety_settings = [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        ]
        config_params["safety_settings"] = safety_settings

        # 3. 温度控制
        if temperature is not None:
            config_params['temperature'] = temperature

        # 4. 针对 Gemini 3.0 的处理 (3.0 依然可以尝试 JSON Mode，或者也降级)
        # 为了稳妥，建议对 2.5 和 3.0 都统一策略：不强制 JSON Mode
        if "gemini-3" in model_name:
            # 即使是 3.0，如果环境里有 AFC 干扰，JSON Mode 也可能导致不稳定
            # 所以这里也去掉 response_mime_type

            target_level = "high"
            if temperature is not None and temperature < 0.3:
                target_level = "low"

            return types.GenerateContentConfig(
                # response_mime_type="application/json", <--- 删除这行
                safety_settings=safety_settings,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=False,
                    thinking_level=target_level
                )
            )

        # 5. Legacy 模型 (Gemini 2.5)
        else:
            # 只要 config 里没有 response_mime_type，也没有 tools (或 tools=None)
            # 就算 SDK 默认带了 AFC，普通 Text Mode 也不会崩溃
            return types.GenerateContentConfig(**config_params)

    def _extract_clean_text(self, response) -> str:
        """
        [诊断模式] 深度打印 API 响应的内部结构
        """
        text_parts = []
        try:
            # 1. 检查 Candidates 是否存在
            if not response.candidates:
                self.logger.error("❌ DIAGNOSTIC: No candidates returned! (Empty Response)")
                return ""

            candidate = response.candidates[0]

            # 2. 打印关键的 Finish Reason (这是破案的关键)
            # 正常应该是 STOP。如果是 SAFETY, RECITATION, 或 OTHER，那就是被拦截了。
            finish_reason = getattr(candidate, 'finish_reason', 'UNKNOWN')
            self.logger.info(f"🔍 DIAGNOSTIC: Finish Reason = {finish_reason}")

            # 3. 检查是否触发了 Function Call (AFC 幽灵)
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    self.logger.error(f"❌ DIAGNOSTIC: Model tried to call a function! Name: {part.function_call.name}")
                    # 如果它试图调用函数，说明 Prompt 或 Tools 配置有问题
                    return ""

                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)

            # 4. 如果没有文本，打印整个 Candidate 结构
            if not text_parts:
                self.logger.error(f"❌ DIAGNOSTIC: No text parts found. Full Candidate dump: {candidate}")

        except Exception as e:
            self.logger.error(f"Diagnostic extraction failed: {e}")
            return ""

        return "".join(text_parts)

    def generate_content(
            self,
            model_name: str,
            prompt: Union[str, List],
            stream: bool = False,
            temperature: Optional[float] = None,
            tools: Optional[List] = None,
            tool_config: Optional[Any] = None,
            **generation_kwargs
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        执行同步的 AI 内容生成请求 (已升级适配 Gemini 3)。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')

        # [修改 1] 调用智能配置构建器
        config = self._build_generation_config(model_name, temperature, tools=tools)

        # 合并 kwargs 中的额外参数 (如果有)
        # 注意：generation_kwargs 中的冲突参数可能需要清理，这里暂时略过

        request_log = {
            "model": model_name,
            "prompt": prompt, # 生产环境建议截断 prompt 日志
            "config_dump": str(config) if config else "None",  # 记录一下 Config 方便调试
            "timestamp": timestamp,
            "caller": self.caller_class
        }
        self._log_to_file("requests", "request_", request_log)

        start_time = datetime.now()
        if tools is None:
            generation_kwargs.pop('tools', None)
            generation_kwargs.pop('tool_config', None)

        try:
            # 定义 API 调用的函数句柄
            api_call = lambda: self._client.models.generate_content(
                model=model_name, contents=prompt,config=config
            )
            response = self._retry_api_call(api_call, "同步生成")

            # [修改 2] 使用安全提取方法，替代 response.text
            full_response_text = self._extract_clean_text(response)

            # 提取 Tokens 用量 (Gemini 3 的结构可能略有不同，建议加 getattr 防御)
            usage_meta = getattr(response, 'usage_metadata', None)
            usage = {
                "model_used": model_name,
                "prompt_tokens": getattr(usage_meta, 'prompt_token_count', 0),
                "completion_tokens": getattr(usage_meta, 'candidates_token_count', 0),
                "total_tokens": getattr(usage_meta, 'total_token_count', 0)
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
        辅助函数：记录错误日志，并重新抛出异常。
        [修改] 增强：识别限流错误并抛出特定异常，供上层 Task 捕获。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        error_msg = str(e)

        error_info = {
            "error_type": type(e).__name__,
            "error_message": error_msg,
            "timestamp": timestamp,
            "context": context,
            "stack_trace": self._get_clean_stacktrace()
        }
        log_subdir = "errors_async" if "async" in context else "errors"
        self._log_to_file(log_subdir, "error_", error_info)

        # --- 抛出强类型异常 ---
        # 识别 Google/Aliyun 常见的限流关键字
        if any(k in error_msg for k in ["429", "ResourceExhausted", "Too Many Requests", "Throttling"]):
            print(f"⚠️ Detected Rate Limit in {context}. Raising RateLimitException.")
            raise RateLimitException(msg=error_msg, provider="Gemini/External") from e

        # 对于其他错误，保持抛出 RuntimeError
        raise RuntimeError(f"{context}失败: {error_msg}") from e

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
        """
        从内容中移除 API key 或 secret 等敏感信息，
        [新增] 并将无法序列化的对象（如图片）转换为字符串占位符。
        """
        if isinstance(content, dict):
            content = content.copy()
            for key in list(content.keys()):
                val = content[key]
                # 1. 敏感信息脱敏
                if "key" in key.lower() or "secret" in key.lower():
                    content[key] = "***REDACTED***"
                # 2. [新增] 递归处理嵌套字典
                else:
                    content[key] = self._sanitize_content(val)

        elif isinstance(content, list):
            # [新增] 处理列表中的图片对象
            return [self._sanitize_content(item) for item in content]

        # [新增] 检查是否是 PIL Image 对象 (通过类名判断，避免引入 PIL 依赖)
        elif hasattr(content, '__class__') and 'Image' in content.__class__.__name__:
             return f"<Image Object: {content.__class__.__name__}>"

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