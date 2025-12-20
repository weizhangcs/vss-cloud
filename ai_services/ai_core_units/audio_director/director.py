# ai_services/ai_core_units/audio_director/director.py

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple

# 引入基础设施
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.mixins import AIServiceMixin

# [新增] 引入分离后的 Schema
from .schemas import AudioDirectorResponse

logger = logging.getLogger(__name__)


class AudioDirector(AIServiceMixin):
    """
    [Core Unit] 通用配音导演 (Generic Audio Director).
    职责：为文本生成 TTS 指令 (情感、语速、停顿)。

    Refactor V6.1:
    - Separated schemas to schemas.py for better maintainability.
    """

    # [Standardized Config]
    DEFAULT_TEMPERATURE = 0.7

    def __init__(self,
                 gemini_processor: GeminiProcessor,
                 prompts_dir: Path):
        self.gemini = gemini_processor
        self.prompts_dir = prompts_dir
        self.logger = logger  # Mixin 需要 self.logger

    def direct_script(self,
                      script: List[Dict],  # 接收 dict 列表 (从 NarrationSnippet dump 出来)
                      lang: str,
                      model: str,
                      style: str = "cinematic",
                      perspective: str = "objective",
                      **kwargs) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        执行导演指令生成。
        kwargs 支持参数:
            - temperature (float): 生成温度
        Returns:
            (modified_script, usage_dict)
        """
        # [Standardized Config]
        temperature = kwargs.get('temperature', self.DEFAULT_TEMPERATURE)
        self.logger.info(f"🎬 Starting Audio Directing (Style: {style}, Temp: {temperature})...")

        # 1. 准备精简输入 (节省 Token)
        simplified_input = [
            {"index": i, "narration": item.get("narration", "")}
            for i, item in enumerate(script)
        ]

        # 2. 构建 Prompt (Mixin V5 Explicit)
        prompt = self._build_prompt(
            prompts_dir=self.prompts_dir,
            prompt_name="narration_audio_director",
            lang=lang,
            # Variables
            style=style,
            perspective=perspective,
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        if not prompt:
            self.logger.warning("Director prompt not found. Skipping directing phase.")
            return script, {}

        # 3. 调用 AI (Schema-First)
        try:
            response_obj, usage_stats = self.gemini.generate_content(
                model_name=model,
                prompt=prompt,
                response_schema=AudioDirectorResponse,  # [Key] 强约束
                temperature=temperature
            )

            # 4. 回填结果
            # response_obj 是 AudioDirectorResponse 实例
            enriched_data = response_obj.enriched_script
            enrich_map = {item.index: item for item in enriched_data}

            # In-Place Modify
            for i, item in enumerate(script):
                directive = enrich_map.get(i)
                if directive:
                    item["tts_instruct"] = directive.tts_instruct
                    item["narration_for_audio"] = directive.narration_for_audio

            self.logger.info("✅ Audio Directing completed.")

            # 返回 usage dict (兼容上层 DubbingEngine 逻辑)
            return script, usage_stats.model_dump()

        except Exception as e:
            self.logger.error(f"Audio Director failed: {e}. Keeping original script.")
            return script, {}