import json
import logging
from typing import List, Dict
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor

logger = logging.getLogger(__name__)

class AudioDirector:
    """
    [Core Unit] 通用配音导演 (Generic Audio Director).
    职责：为文本生成 TTS 指令 (情感、语速、停顿)。
    """
    # ... (原代码内容保持不变，仅包名变更) ...
    def __init__(self, gemini_processor: GeminiProcessor):
        self.gemini = gemini_processor

    def enrich_script(self,
                      script: List[Dict],
                      model: str,
                      director_prompt_template: str,
                      style_desc: str,
                      perspective_desc: str) -> List[Dict]:

        logger.info("Starting Audio Directing...")

        # 简化输入以节省 Token
        simplified_input = [
            {"index": i, "narration": item["narration"]}
            for i, item in enumerate(script)
        ]

        prompt = director_prompt_template.format(
            style=style_desc,
            perspective=perspective_desc,
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        try:
            response, _ = self.gemini.generate_content(
                model_name=model,
                prompt=prompt,
                temperature=0.7
            )
            enriched_data = response.get("enriched_script", [])

            enrich_map = {item["index"]: item for item in enriched_data}

            # 回填结果
            for i, item in enumerate(script):
                directive = enrich_map.get(i)
                if directive:
                    item["tts_instruct"] = directive.get("tts_instruct")
                    item["narration_for_audio"] = directive.get("narration_for_audio")
                else:
                    item["tts_instruct"] = "Speak naturally."
                    # 如果没有 narration_for_audio，后续流程通常会回退到 narration，这里可不填

        except Exception as e:
            logger.error(f"Audio Director failed: {e}. Keeping original script.")
            # 失败不阻断，静默返回原脚本

        return script