# task_manager/handlers/__init__.py
from .base import BaseTaskHandler
from .registry import HandlerRegistry

# 导入所有 Handler 模块以触发装饰器注册
from . import rag
from . import narration
from . import character
from . import editing
from . import dubbing