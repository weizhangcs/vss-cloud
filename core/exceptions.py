# core/exceptions.py
from rest_framework.exceptions import APIException
from .error_codes import ErrorCode


class BizException(APIException):
    """
    通用业务异常基类。
    """
    status_code = 400  # 默认 HTTP 状态码
    default_detail = 'Service Error'

    def __init__(self, error_code: ErrorCode, msg=None, status_code=None, data=None):
        self.detail = msg or error_code.msg
        self.code = error_code.code
        self.data = data  # 可选的额外数据
        if status_code:
            self.status_code = status_code


class RateLimitException(BizException):
    """
    [新增] 专门用于标识第三方 API 限流的异常。
    GeminiProcessor 抛出此异常，Task 层捕获此异常进行重试。
    """

    def __init__(self, msg="Rate limit exceeded", provider="unknown"):
        super().__init__(
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            msg=f"Rate limit exceeded for {provider}: {msg}",
            status_code=429
        )