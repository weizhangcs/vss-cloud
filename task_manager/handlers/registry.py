# task_manager/handlers/registry.py
from typing import Dict, Type
from task_manager.models import Task
from .base import BaseTaskHandler


class HandlerRegistry:
    """
    任务处理器注册表。
    负责维护 TaskType 到 Handler 类的映射关系。
    """
    _registry: Dict[str, Type[BaseTaskHandler]] = {}

    @classmethod
    def register(cls, task_type: str):
        """
        装饰器：将一个 Handler 类注册到指定的 TaskType。

        用法:
            @HandlerRegistry.register(Task.TaskType.DEPLOY_RAG_CORPUS)
            class RagDeploymentHandler(BaseTaskHandler):
                ...
        """

        def wrapper(handler_cls):
            if not issubclass(handler_cls, BaseTaskHandler):
                raise ValueError(f"Handler {handler_cls.__name__} must inherit from BaseTaskHandler")

            cls._registry[task_type] = handler_cls
            return handler_cls

        return wrapper

    @classmethod
    def get_handler(cls, task_type: str) -> BaseTaskHandler:
        """
        获取指定任务类型的 Handler 实例。
        """
        handler_cls = cls._registry.get(task_type)
        if not handler_cls:
            # 如果找不到对应的 Handler，说明我们忘记注册了，或者这就是一个不支持的任务类型
            raise ValueError(f"No handler registered for task type: {task_type}")

        # 实例化 Handler 并返回
        return handler_cls()