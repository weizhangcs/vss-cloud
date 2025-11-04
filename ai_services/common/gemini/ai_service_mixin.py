# ai_services/common/gemini/
# 描述: 一个包含所有AI服务通用能力的Mixin类。

import json
from pathlib import Path
from typing import Dict, Any, Union, List, Optional
from datetime import datetime
from functools import lru_cache
from collections import defaultdict


class AIServiceMixin:
    """
    一个可复用的“混入”类，为服务提供与AI交互相关的通用方法，
    包括加载提示词、构建Prompt、聚合用量数据等。
    """

    @lru_cache(maxsize=4)
    def _load_prompt_template(self, lang: str, prompt_name: str) -> str:
        """根据名称从服务的prompts目录加载并缓存Prompt模板。"""
        # self.prompts_dir 和 self.logger 将由继承此Mixin的子类提供
        template_file = self.prompts_dir / f"{prompt_name}_{lang}.txt"
        if not template_file.is_file():
            raise FileNotFoundError(
                f"Prompt template not found for service [{self.__class__.__name__}]: {template_file}")
        self.logger.info(f"Loading prompt template from {template_file}...")
        return template_file.read_text(encoding='utf-8')

    def _load_localization_file(self, localization_path: Path, lang: str):
        """加载当前服务专属的语言包文件。"""
        if not localization_path.is_file():
            self.logger.warning(f"语言包文件未找到: {localization_path}。将使用默认文本。")
            self.labels = defaultdict(str)
            return

        with localization_path.open('r', encoding='utf-8') as f:
            all_loc_data = json.load(f)

        self.labels = all_loc_data.get(lang, all_loc_data.get('en', {}))
        if not self.labels:
            self.logger.error(f"在语言包中未能找到 {lang} 或 en 的配置。")

    def _build_prompt(self, prompt_name: str, **kwargs) -> str:
        """
        一个通用的Prompt构建方法。
        """
        lang = kwargs.get('lang', 'en')
        template = self._load_prompt_template(lang, prompt_name)
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in template:
                str_value = str(value) if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False,
                                                                                              indent=2)
                template = template.replace(placeholder, str_value)
        return template

    def _aggregate_usage(self, total_usage: dict, new_usage: dict):
        """
        一个通用的、动态的usage聚合方法。
        """
        if not new_usage: return
        for key, value in new_usage.items():
            if isinstance(value, (int, float)):
                total_usage[key] = total_usage.get(key, 0) + value
        if 'start_time_utc' in new_usage:
            if 'session_start_time' not in total_usage or new_usage['start_time_utc'] < total_usage.get(
                    'session_start_time'):
                total_usage['session_start_time'] = new_usage['start_time_utc']
        if 'end_time_utc' in new_usage:
            if 'session_end_time' not in total_usage or new_usage['end_time_utc'] > total_usage.get('session_end_time'):
                total_usage['session_end_time'] = new_usage['end_time_utc']