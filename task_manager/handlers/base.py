# task_manager/handlers/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any
import logging
from task_manager.models import Task

# 使用当前模块的 logger，方便后续按模块过滤日志
logger = logging.getLogger(__name__)


class BaseTaskHandler(ABC):
    """
    所有任务处理器的抽象基类。
    每个具体的业务逻辑（如 RAG 部署、解说生成）都应继承此类。
    """

    def __init__(self):
        self.logger = logger

    @abstractmethod
    def handle(self, task: Task) -> Dict[str, Any]:
        """
        执行具体的任务逻辑。

        Args:
            task (Task): 当前要执行的任务实例。

        Returns:
            Dict[str, Any]: 任务执行的结果数据，将存入 Task.result。

        Raises:
            Exception: 如果执行过程中出现错误，应直接抛出异常，
                       由上层调度器捕获并标记任务失败。
        """
        pass