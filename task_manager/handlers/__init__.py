# task_manager/handlers/__init__.py
from .base import BaseTaskHandler
from .registry import HandlerRegistry

# 导入所有 Handler 模块以触发装饰器注册
from . import rag
from . import narration
from . import character
from . import editing
from . import dubbing
from . import localization
from . import visual_analysis
from . import subtitle_context
from . import character_pre_annotator
from . import scene_pre_annotator
from . import visual_analyzer
from . import slice_grouper
from . import subtitle_merger