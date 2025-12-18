import json
import logging
import copy
from pathlib import Path
from typing import Dict, Any, List

from pydantic import ValidationError

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.mixins import AIServiceMixin

# [New] 引入 Core Units & Components
from ai_services.biz_services.localization.components.pacing_checker import LocalizationPacingChecker
from ai_services.ai_core_units.text_refiner.refiner import TextRefiner
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from ai_services.biz_services.localization.schemas import LocalizationServiceParams, LocalizationResult
from ai_services.biz_services.narration.schemas import NarrationSnippet

logger = logging.getLogger(__name__)


class ContentLocalizer(AIServiceMixin):
    """
    [Service Layer] 内容本地化服务 (V6 Architecture).
    Flow: Translate (Context Aware) -> Pacing Check -> Refine (Localization Specific)
    """

    def __init__(self,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator,
                 prompts_dir: Path,
                 logger: logging.Logger):
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = prompts_dir  # localization/prompts
        self.logger = logger

    def _load_prompt_template(self, lang: str, template_name: str) -> str:
        """加载 Localization 业务包下的 Prompt"""
        # 尝试 localization_refine_en.txt
        path = self.prompts_dir / f"{template_name}_{lang}.txt"
        if not path.exists() and lang != 'en':
            path = self.prompts_dir / f"{template_name}_en.txt"

        if path.exists():
            return path.read_text(encoding='utf-8')
        return ""

    def execute(self,
                master_script_data: Dict[str, Any],
                config: LocalizationServiceParams,  # [Type Change] 接收对象
                dataset: NarrativeDataset) -> Dict[str, Any]:

        self.logger.info(f"Starting Localization: {config.source_lang} -> {config.target_lang}")

        # 1. 初始化组件
        # [Fix] 语速逻辑修复：config.speaking_rate 现在是 None (除非用户指定)，
        # 所以 PacingChecker 会正确使用内部定义的 DEFAULT_RATES (如 en=2.5, zh=3.8)
        pacing_checker = LocalizationPacingChecker(
            dataset=dataset,
            target_lang=config.target_lang,
            user_speaking_rate=config.speaking_rate,
            tolerance_ratio=config.tolerance_ratio,
            logger=self.logger
        )

        refiner = TextRefiner(self.gemini_processor)
        refine_template = self._load_prompt_template(config.target_lang, "localization_refine")

        # 2. 核心翻译
        input_script = master_script_data.get("narration_script", [])
        rag_context = master_script_data.get("rag_context_snapshot", "")

        # [Fix] 使用 config 中的源语言
        translated_script = self._translate_script(
            input_script,
            src_lang=config.source_lang,
            tgt_lang=config.target_lang,
            context=rag_context,
            model=config.model
        )

        # 3. 校验与精炼
        final_script_objs = []

        for index, snippet in enumerate(translated_script):
            # Sanitize
            snippet["narration"] = snippet["narration"]

            # Pacing Check
            is_ok, info = pacing_checker.check_pacing(snippet)

            if not is_ok and info['real_visual_duration'] > 0.1:
                self.logger.warning(f"Snippet {index} overflow ({info['overflow_sec']}s). Refining...")

                target_count = int(info["real_visual_duration"] * pacing_checker.speaking_rate)
                safe_target_count = max(5, target_count)  # 至少5个单位

                refined_text = refiner.refine_content(
                    content=snippet["narration"],
                    prompt_template=refine_template,
                    model_name=config.model,
                    max_seconds=info["real_visual_duration"],
                    # 这里的参数名最好在 Prompt 中也做相应兼容，或者我们统一传 target_length
                    # 暂时为了兼容现有的 prompt 变量名 {max_chars}，我们把计算出的 单词数/字数 传进去
                    # 但最好在 Prompt 里把 {max_chars} 改名为 {target_length} 并在 Prompt 里描述 unit
                    max_chars=safe_target_count,
                    style=""
                )

                if refined_text:
                    snippet["narration"] = refined_text
                    is_ok_now, new_info = pacing_checker.check_pacing(snippet)
                    info = new_info
                    snippet["metadata"] = info
                    snippet["metadata"]["refined"] = True
                else:
                    snippet["metadata"] = info
                    snippet["metadata"]["validation_error"] = "Refine Failed"
            else:
                snippet["metadata"] = info

            # 清理
            snippet.pop("tts_instruct", None)
            snippet.pop("narration_for_audio", None)

            # [Convert to Schema Object]
            try:
                snippet_obj = NarrationSnippet(**snippet)
                final_script_objs.append(snippet_obj)
            except Exception as e:
                self.logger.error(f"Snippet Schema Validation Failed at index {index}: {e}")
                # 即使失败也尽量不崩溃，可以跳过或记录错误
                continue

        # 4. 结果封装 (使用 LocalizationResult)
        result = LocalizationResult(
            generation_date=master_script_data.get("generation_date"),
            asset_name=master_script_data.get("asset_name"),
            source_corpus=master_script_data.get("source_corpus"),
            source_lang=config.source_lang,  # [Fix] 记录源语言
            target_lang=config.target_lang,
            rag_context_snapshot=rag_context,
            narration_script=final_script_objs,
            ai_total_usage={"note": "Localization + Refine"}
        )

        return result.model_dump()

    def _translate_script(self, script: List[Dict], src_lang: str, tgt_lang: str, context: str, model: str) -> List[
        Dict]:
        """
        利用 RAG 上下文进行精准翻译
        """
        if not script: return []

        simplified_input = [
            {"index": i, "narration": item["narration"]}
            for i, item in enumerate(script)
        ]

        # 这里的 Template 还是用 narration_translator (在 prompts_dir 下)
        # 逻辑：如果 tgt_lang 是支持的语言，用对应的模版；否则用 en 模版
        prompt_lang = tgt_lang if tgt_lang in ["zh", "en", "fr"] else "en"
        translator_template = self._load_prompt_template(prompt_lang, "narration_translator")

        if not translator_template:
            # Fallback logic if needed, or raise Error
            self.logger.error("Translator template missing.")
            return script

        prompt = translator_template.format(
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            rag_context=context,
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        try:
            self.logger.info("Invoking LLM for Translation...")
            response_data, _ = self.gemini_processor.generate_content(
                model_name=model,
                prompt=prompt,
                temperature=0.3
            )
            translated_list = response_data.get("translated_script", [])

            # Map back by index
            trans_map = {}
            for t_item in translated_list:
                try:
                    idx = int(t_item.get("index", -1))
                    if idx >= 0:
                        trans_map[idx] = t_item.get("narration", "")
                except (ValueError, TypeError):
                    continue

            # Merge
            merged_script = copy.deepcopy(script)
            for i, item in enumerate(merged_script):
                if i in trans_map:
                    item["narration_source"] = item["narration"]  # 保留原文
                    item["narration"] = trans_map[i]  # 更新为译文

            return merged_script

        except Exception as e:
            self.logger.error(f"Translation failed: {e}. Returning original.")
            return script