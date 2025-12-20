import json
import time
import inspect
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union, List, Callable, Type, Tuple, TypeVar

from google import genai
from google.genai import types
from google.api_core import exceptions
from google.genai.errors import ServerError
from pydantic import BaseModel

from core.exceptions import RateLimitException
from .schemas import UsageStats

# 定义泛型 T
T = TypeVar("T", bound=BaseModel)


class GeminiProcessor:
    """
    [Infrastructure] Gemini API 核心处理器 (Schema-First & Type-Safe Edition).
    """

    _MAX_RETRIES = 3
    _INITIAL_RETRY_DELAY = 1
    _MAX_RETRY_DELAY = 10
    _RETRYABLE_ERRORS = (
        exceptions.ServiceUnavailable, ServerError, exceptions.TooManyRequests,
        exceptions.InternalServerError, exceptions.GatewayTimeout,
        exceptions.ResourceExhausted
    )

    def __init__(self,
                 api_key: str,
                 logger: logging.Logger,
                 debug_mode: bool = False,
                 debug_dir: Union[str, Path] = "gemini_debug",
                 client: Optional[genai.Client] = None,
                 caller_class: Optional[str] = None):

        self.logger = logger
        self.debug_mode = debug_mode
        self.caller_class = caller_class or self._get_caller_class_name()

        if client:
            self._client = client
        elif api_key:
            try:
                self._client = genai.Client(api_key=api_key)
            except Exception as e:
                self.logger.error(f"Client Init Failed: {e}")
                raise
        else:
            raise ValueError("必须提供 API Key 或 Client 实例。")

        # [Fix] 优化目录创建逻辑：收窄异常并处理失败回退
        if self.debug_mode:
            self.debug_dir = Path(debug_dir)
            try:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                # 如果创建目录失败（如权限不足），降级为不记录日志，而不是崩溃或静默失败
                self.logger.warning(f"Failed to create debug directory '{self.debug_dir}': {e}. Logging disabled.")
                self.debug_dir = None
        else:
            self.debug_dir = None

    def _get_default_safety_settings(self) -> List[types.SafetySetting]:
        return [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

    def _prepare_config(self,
                        temperature: Optional[float],
                        schema: Optional[Type[BaseModel]],
                        external_config: Optional[types.GenerateContentConfig],
                        extra_kwargs: Dict[str, Any]
                        ) -> types.GenerateContentConfig:
        """
        [Fix] 增强配置构建器：支持 Thinking Config 及更多生成参数
        """
        # 1. 基础配置
        config = external_config if external_config else types.GenerateContentConfig()

        # 2. 安全设置
        if not hasattr(config, 'safety_settings') or not config.safety_settings:
            config.safety_settings = self._get_default_safety_settings()

        # 3. 显式参数覆盖 (Temperature)
        # 注意：对于 Thinking 模型，通常不需要手动设置 Temperature (或设为 0)
        # 这里保留覆盖逻辑，但业务层需知晓
        if temperature is not None:
            config.temperature = temperature

        # 4. [优化] 扩展参数白名单，支持 Thinking 和惩罚项
        # 参考 SDK: google.genai.types.GenerateContentConfig
        valid_gen_keys = {
            'top_p', 'top_k', 'max_output_tokens', 'stop_sequences', 'candidate_count',
            'presence_penalty', 'frequency_penalty', 'seed', 'response_logprobs', 'logprobs',
            'thinking_config',  # <--- [核心新增] 支持思考配置
            'system_instruction'  # <--- 支持从 kwargs 传入系统指令
        }

        for k, v in extra_kwargs.items():
            if k in valid_gen_keys:
                setattr(config, k, v)

        # 5. Schema 注入
        if schema:
            config.response_mime_type = "application/json"
            config.response_schema = schema

        return config

    def generate_content(
            self,
            model_name: str,
            prompt: Union[str, List],
            response_schema: Optional[Type[T]] = None,
            temperature: Optional[float] = None,
            config: Optional[types.GenerateContentConfig] = None,
            **kwargs
    ) -> Tuple[Union[T, str], UsageStats]:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        start_time = datetime.now()

        final_config = self._prepare_config(temperature, response_schema, config, kwargs)

        self._log_payload("request", timestamp, {
            "model": model_name,
            "prompt_preview": str(prompt)[:200],
            "schema": response_schema.__name__ if response_schema else "None",
            "config": str(final_config)
        })

        def api_call():
            return self._client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=final_config,
            )

        try:
            response, retry_count = self._retry_api_call(api_call, f"Gen({model_name})")

            result, usage = self._process_response(
                response,
                model_name,
                start_time,
                response_schema,
                request_count=1 + retry_count
            )

            self._log_payload("response", timestamp, {
                "usage": usage.model_dump(),
                "result_preview": str(result)[:200]
            })

            return result, usage

        except Exception as e:
            self._log_error(e, "GenerateContent", timestamp)
            raise

    # -------------------------------------------------------------------------
    # 内部逻辑
    # -------------------------------------------------------------------------

    def _process_response(self,
                          response: Any,
                          model_name: str,
                          start_time: datetime,
                          schema: Optional[Type[T]],
                          request_count: int) -> Tuple[Union[T, str], UsageStats]:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        meta = getattr(response, 'usage_metadata', None)

        # [Fix] 安全提取函数：处理 meta 为空 以及 属性值为 None 的情况
        def get_cnt(key):
            if not meta: return 0
            val = getattr(meta, key, 0)
            return val if val is not None else 0

        usage = UsageStats(
            model_used=model_name,
            prompt_tokens=get_cnt('prompt_token_count'),
            cached_tokens=get_cnt('cached_content_token_count'), # [核心修复] None -> 0
            completion_tokens=get_cnt('candidates_token_count'),
            total_tokens=get_cnt('total_token_count'),
            duration_seconds=round(duration, 4),
            request_count=request_count,
            timestamp=end_time.isoformat()
        )

        if schema:
            if hasattr(response, 'parsed') and response.parsed:
                return response.parsed, usage
            else:
                raw_text = getattr(response, 'text', '')
                try:
                    return schema.model_validate_json(raw_text), usage
                except Exception as e:
                    raise ValueError(f"SDK failed to parse schema. Raw text: {raw_text[:100]}...") from e
        else:
            return getattr(response, 'text', ''), usage

    def _retry_api_call(self, func: Callable, context: str) -> Tuple[Any, int]:
        retries = 0
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return func(), retries
            except self._RETRYABLE_ERRORS as e:
                if attempt == self._MAX_RETRIES:
                    if "429" in str(e) or "ResourceExhausted" in str(e):
                        raise RateLimitException(msg=str(e), provider="Gemini") from e
                    raise

                retries += 1
                delay = min(self._INITIAL_RETRY_DELAY * (2 ** attempt), self._MAX_RETRY_DELAY)
                self.logger.warning(f"⚠️ {context} Retry {attempt + 1}: {e}. Wait {delay}s.")
                time.sleep(delay)
        return None, retries

    # -------------------------------------------------------------------------
    # 日志辅助
    # -------------------------------------------------------------------------
    def _log_payload(self, phase: str, timestamp: str, data: Any):
        if not self.debug_dir: return
        try:
            def default_ser(obj):
                return str(obj)

            path = self.debug_dir / f"{self.caller_class}_{timestamp}_{phase}.json"
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=default_ser),
                encoding='utf-8'
            )
        # [Fix] 收窄异常范围：只捕获IO错误和序列化错误
        except (OSError, TypeError):
            pass

    def _log_error(self, e: Exception, context: str, timestamp: str):
        if not self.debug_dir: return
        self._log_payload("error", timestamp, {
            "context": context,
            "error_type": type(e).__name__,
            "message": str(e)
        })

    def _get_caller_class_name(self) -> str:
        frame = inspect.currentframe()
        while frame:
            frame = frame.f_back
            if not frame: break
            if 'self' in frame.f_locals:
                return frame.f_locals['self'].__class__.__name__
        return "Unknown"