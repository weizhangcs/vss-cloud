# core/handlers.py
from rest_framework.views import exception_handler
from rest_framework.response import Response
from .exceptions import BizException
from .error_codes import ErrorCode


def vss_exception_handler(exc, context):
    """
    全局异常处理函数：将所有异常转换为统一的 {code, message, data} 格式。
    """
    # 1. 先让 DRF 处理基础异常（如 404, 403, ValidationError）
    response = exception_handler(exc, context)

    # 2. 构造标准响应结构
    payload = {
        "code": ErrorCode.UNKNOWN_ERROR.code,
        "message": str(exc),
        "data": None
    }

    # 3. 处理 BizException (我们自定义的业务异常)
    if isinstance(exc, BizException):
        payload["code"] = exc.code
        payload["message"] = exc.detail
        payload["data"] = exc.data
        if response is None:
            # 如果 DRF 没处理（比如是继承自 Exception 而非 APIException），我们需要手动创建 Response
            response = Response(payload, status=exc.status_code)
        else:
            response.data = payload

    # 4. 处理 DRF 原生异常
    elif response is not None:
        if response.status_code == 400:
            payload["code"] = ErrorCode.INVALID_PARAM.code
            # DRF 的 ValidationError通常是一个 dict 或 list，我们把它放进 data 里，message 保持简短
            payload["message"] = "Invalid parameters."
            payload["data"] = response.data
        elif response.status_code == 401:
            payload["code"] = ErrorCode.UNAUTHORIZED.code
            payload["message"] = "Authentication credentials were not provided."
        elif response.status_code == 403:
            payload["code"] = ErrorCode.PERMISSION_DENIED.code
            payload["message"] = "You do not have permission to perform this action."
        elif response.status_code == 404:
            payload["code"] = ErrorCode.NOT_FOUND.code
            payload["message"] = "Resource not found."
        elif response.status_code == 429:  # DRF 自身的限流
            payload["code"] = ErrorCode.RATE_LIMIT_EXCEEDED.code
            payload["message"] = "Request was throttled."

        # 覆盖 DRF 默认的 data
        response.data = payload

    # 5. 如果 response 依然是 None (例如代码里有未捕获的 KeyError)，
    # Django 默认会返回 500 HTML 页面。
    # 如果希望 API 永远返回 JSON，可以在这里 catch Exception 并返回 500 Response。
    # 但为了开发调试方便，我们通常保留 Django 的 500 页面逻辑（DEBUG=True时）。

    return response