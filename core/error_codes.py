# core/error_codes.py
from enum import Enum


class ErrorCode(Enum):
    # --- 0-999: 基础系统级 ---
    SUCCESS = (0, "Success")
    UNKNOWN_ERROR = (1000, "Internal Server Error")
    INVALID_PARAM = (1001, "Invalid Parameters")
    UNAUTHORIZED = (1002, "Authentication Failed")
    PERMISSION_DENIED = (1003, "Permission Denied")
    NOT_FOUND = (1004, "Resource Not Found")

    # --- 2000-2999: 任务调度级 ---
    TASK_CREATION_FAILED = (2001, "Failed to create task")
    TASK_NOT_FOUND = (2002, "Task not found")
    TASK_ALREADY_FINISHED = (2003, "Task already completed or failed")
    TASK_EXECUTION_FAILED = (2004, "Task execution failed")

    # --- 3000-3999: AI 服务级 ---
    RAG_DEPLOYMENT_ERROR = (3001, "RAG deployment failed")
    LLM_INFERENCE_ERROR = (3002, "LLM inference failed")
    TTS_GENERATION_ERROR = (3003, "TTS generation failed")
    PAYLOAD_VALIDATION_ERROR = (3004, "Task payload validation failed")

    # --- 4000-4999: 外部依赖级 ---
    THIRD_PARTY_API_ERROR = (4001, "Third-party API call failed")
    RATE_LIMIT_EXCEEDED = (4002, "Rate limit exceeded (Quota exhausted)")
    FILE_IO_ERROR = (4003, "File Input/Output error")

    @property
    def code(self):
        return self.value[0]

    @property
    def msg(self):
        return self.value[1]