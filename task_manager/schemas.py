# task_manager/schemas.py
from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from .models import Task

class TaskCreateRequest(BaseModel):
    """
    任务创建请求 Schema
    """
    # 直接使用 Model 中的 Enum，保证类型安全
    task_type: str = Field(..., description="任务类型 (e.g., GENERATE_NARRATION)")
    payload: Dict[str, Any] = Field(default_factory=dict, description="任务参数载荷")

    @field_validator('task_type')
    @classmethod
    def validate_task_type(cls, v: str) -> str:
        # 定义允许云端接收的任务白名单
        allowed = [
            Task.TaskType.CHARACTER_IDENTIFIER,
            Task.TaskType.DEPLOY_RAG_CORPUS,
            Task.TaskType.GENERATE_NARRATION,
            Task.TaskType.GENERATE_EDITING_SCRIPT,
            Task.TaskType.LOCALIZE_NARRATION,
            Task.TaskType.GENERATE_DUBBING,
            Task.TaskType.VISUAL_ANALYSIS,
            Task.TaskType.SUBTITLE_CONTEXT,
            Task.TaskType.CHARACTER_PRE_ANNOTATOR,
            Task.TaskType.SCENE_PRE_ANNOTATOR,
            Task.TaskType.VISUAL_ANALYZER,
            Task.TaskType.SUBTITLE_MERGER

        ]
        # 注意：这里 v 是字符串，需要和 Model Enum 的 value 进行比对
        if v not in allowed:
            raise ValueError(f"Invalid task_type: {v}. Allowed: {allowed}")
        return v        # 注意：这里 v 是字符串，需要和 Model Enum 的 value 进行比对

class TaskResponse(BaseModel):
    """
    任务详情返回 Schema
    """
    id: int
    status: str
    task_type: str
    result: Optional[Dict[str, Any]] = None
    created: datetime
    modified: datetime
    download_url: Optional[str] = None # 自定义计算字段