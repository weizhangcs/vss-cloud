import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor

logger = logging.getLogger(__name__)


class AudioDirector:
    """
    [Core Unit] 通用配音导演 (Generic Audio Director).
    职责：为文本生成 TTS 指令 (情感、语速、停顿)。
    特性：Self-Contained Prompt Loading.
    """

    def __init__(self,
                 gemini_processor: GeminiProcessor,
                 prompts_dir: Path):
        self.gemini = gemini_processor
        self.prompts_dir = prompts_dir

    def _load_prompt_template(self, lang: str) -> str:
        """加载导演提示词"""
        # 命名约定: narration_audio_director_{lang}.txt
        # 默认回退到 zh 或 en
        candidates = [
            self.prompts_dir / f"narration_audio_director_{lang}.txt",
            self.prompts_dir / "narration_audio_director_en.txt",
            self.prompts_dir / "narration_audio_director_zh.txt",
        ]
        for p in candidates:
            if p.exists():
                return p.read_text(encoding='utf-8')
        return ""

    def direct_script(self,
                      script: List[Dict],  # 接收 dict 列表 (从 NarrationSnippet dump 出来)
                      lang: str,
                      model: str,
                      style: str = "cinematic",
                      perspective: str = "objective") -> List[Dict]:

        logger.info(f"🎬 Starting Audio Directing (Style: {style})...")

        template_content = self._load_prompt_template(lang)
        if not template_content:
            logger.warning("Director prompt not found. Skipping directing phase.")
            return script

        # 简化输入以节省 Token
        simplified_input = [
            {"index": i, "narration": item.get("narration", "")}
            for i, item in enumerate(script)
        ]

        prompt = template_content.format(
            style=style,
            perspective=perspective,
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        try:
            response, usage = self.gemini.generate_content(
                model_name=model,
                prompt=prompt,
                temperature=0.7
            )
            # 假设 response 是结构化的 {"enriched_script": [...]}
            enriched_data = response.get("enriched_script", [])

            enrich_map = {item.get("index"): item for item in enriched_data}

            # 回填结果 (In-Place Modify)
            for i, item in enumerate(script):
                directive = enrich_map.get(i)
                if directive:
                    item["tts_instruct"] = directive.get("tts_instruct")
                    item["narration_for_audio"] = directive.get("narration_for_audio")

            logger.info("✅ Audio Directing completed.")
            return script, usage

        except Exception as e:
            logger.error(f"Audio Director failed: {e}. Keeping original script.")
            return script, {}