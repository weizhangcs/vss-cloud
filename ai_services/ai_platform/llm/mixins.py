import json
from pathlib import Path
from typing import Dict, Any, Union
from functools import lru_cache
from collections import defaultdict
from pydantic import BaseModel


class AIServiceMixin:
    """
    [Infrastructure] 通用 AI 服务能力混入类 (V5 Final Strict).

    Design Philosophy:
    - Explicit Dependencies: prompts_dir 和 lang 均为核心定位参数，必须显式定义。
    - Stateless Logic: 核心逻辑不依赖实例状态。
    """

    @staticmethod
    @lru_cache(maxsize=32)
    def _read_template_file(base_dir: Path, lang: str, prompt_name: str) -> str:
        """
        [Static Core] 纯函数式的文件读取，底层 IO 操作。
        缓存 Key 为 (Path, str, str)，实现跨实例缓存共享。
        """
        # 1. 构造路径
        target_file = base_dir / f"{prompt_name}_{lang}.txt"

        # 2. 检查与回退 (Fallback to English)
        if not target_file.is_file() and lang != 'en':
            target_file = base_dir / f"{prompt_name}_en.txt"

        if not target_file.is_file():
            # 这里的异常抛出是合理的，因为这是 IO 层的“未找到文件”事实
            raise FileNotFoundError(f"Template '{prompt_name}' not found in {base_dir} (lang={lang})")

        # 3. 读取内容
        return target_file.read_text(encoding='utf-8')

    def _load_prompt_template(self, prompts_dir: Path, lang: str, prompt_name: str) -> str:
        """
        [Wrapper] 负责日志记录和异常捕获，依赖显式传入的参数。
        """
        try:
            # 调用静态缓存方法
            return self._read_template_file(prompts_dir, lang, prompt_name)
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"Failed to load template '{prompt_name}': {e}")
            return ""

    def _build_prompt(self, prompts_dir: Path, prompt_name: str, lang: str = "en", **kwargs) -> str:
        """
        通用的 Prompt 构建方法。

        Args:
            prompts_dir: [Explicit] 提示词目录 (Path对象)
            prompt_name: [Explicit] 模板文件名前缀
            lang:        [Explicit] 语言代码 (默认为 'en')
            **kwargs:    仅用于填充模板的变量 (Prompt Context)
        """
        # 显式传递 lang，不再从 kwargs 中“打捞”
        template = self._load_prompt_template(prompts_dir, lang, prompt_name)

        if not template:
            return ""

        # 简单的字符串替换
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in template:
                if isinstance(value, (dict, list)):
                    str_value = json.dumps(value, ensure_ascii=False, indent=2)
                else:
                    str_value = str(value)
                template = template.replace(placeholder, str_value)
        return template

    def _load_localization_file(self, localization_path: Path, lang: str):
        """
        加载服务专属的语言包文件 (UI Labels)。
        """
        if not localization_path.is_file():
            if hasattr(self, 'logger'):
                self.logger.warning(f"Localization file missing: {localization_path}")
            self.labels = defaultdict(str)
            return

        try:
            with localization_path.open( encoding='utf-8') as f:
                all_loc_data = json.load(f)
            self.labels = all_loc_data.get(lang, all_loc_data.get('en', {}))
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"Failed to load localization: {e}")
            self.labels = {}

    def _aggregate_usage(self,
                         total_usage: Dict[str, Any],
                         new_usage: Union[Dict, BaseModel, None]):
        """
        聚合用量数据 (支持 Pydantic 对象)。
        """
        if not new_usage:
            return

        # 1. 归一化
        if isinstance(new_usage, BaseModel):
            data = new_usage.model_dump()
        else:
            data = new_usage

        # 2. 累加数值
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "timestamp" not in key and "time" not in key:
                    total_usage[key] = total_usage.get(key, 0) + value

        # 3. 更新时间窗口
        current_time = data.get('timestamp') or data.get('end_time_utc')

        if current_time:
            if 'session_start_time' not in total_usage:
                total_usage['session_start_time'] = current_time
            if current_time < total_usage['session_start_time']:
                total_usage['session_start_time'] = current_time

            if 'session_end_time' not in total_usage:
                total_usage['session_end_time'] = current_time
            if current_time > total_usage['session_end_time']:
                total_usage['session_end_time'] = current_time