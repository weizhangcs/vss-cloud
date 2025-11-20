import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import vertexai
from vertexai import rag

from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.rag.schemas import load_i18n_strings
from .query_builder import NarrationQueryBuilder
from .context_enhancer import ContextEnhancer
# [新增] 导入校验器
from .validator import NarrationValidator


class NarrationGeneratorV2(AIServiceMixin):
    SERVICE_NAME = "narration_generator"

    # 最大重试次数，防止死循环
    MAX_REFINE_RETRIES = 2

    def __init__(self,
                 project_id: str,
                 location: str,
                 prompts_dir: Path,
                 metadata_dir: Path,
                 rag_schema_path: Path,
                 logger: logging.Logger,
                 work_dir: Path,
                 gemini_processor: GeminiProcessor):

        self.project_id = project_id
        self.location = location
        self.prompts_dir = prompts_dir
        self.metadata_dir = metadata_dir
        self.logger = logger
        self.work_dir = work_dir
        self.gemini_processor = gemini_processor

        vertexai.init(project=self.project_id, location=self.location)
        load_i18n_strings(rag_schema_path)

        self.styles_config = self._load_json_config("styles.json")
        self.perspectives_config = self._load_json_config("perspectives.json")
        self.query_templates = self._load_json_config("query_templates.json")
        # [新增] 加载优化模版
        self.refine_templates = self._load_json_config("refine_templates.json")

        self.logger.info("NarrationGeneratorV2 initialized (Full Configuration).")

    def _load_json_config(self, filename: str) -> Dict:
        path = self.metadata_dir / filename
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        corpora = rag.list_corpora()
        corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)
        if not corpus:
            raise RuntimeError(f"RAG Corpus not found: '{corpus_display_name}'")
        return corpus

    def execute(self,
                series_name: str,
                corpus_display_name: str,
                blueprint_path: Path,
                config: Dict[str, Any]) -> Dict[str, Any]:

        self.logger.info(f"Starting V2 Generation for: {series_name}")

        # --- Stage 1: Intent-Based Retrieval ---
        # (保持不变)
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        query = qb.build(series_name, config)

        rag_corpus = self._get_rag_corpus(corpus_display_name)
        top_k = config.get("rag_top_k", 50)

        self.logger.info(f"Retrieving from RAG (top_k={top_k})...")
        retrieval_response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
            text=query,
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
        )
        raw_chunks = retrieval_response.contexts.contexts

        # --- Stage 2: Context Enhancement ---
        # (保持不变)
        enhancer = ContextEnhancer(blueprint_path, self.logger)
        enhanced_context = enhancer.enhance(raw_chunks, config)

        if not enhanced_context or "No relevant scenes" in enhanced_context:
            self.logger.warning("Context enhancement resulted in empty content.")
            return {"narration_script": [], "metadata": {"status": "empty_context"}}

        # --- Stage 3: Synthesis (4-Slot Assembly) ---
        # (保持不变，增加了 target_duration_minutes 的注入)
        lang = config.get("lang", "zh")
        control = config.get("control_params", {})

        perspective_key = control.get("perspective", "third_person")
        persp_templates = self.perspectives_config.get(lang, {})
        perspective_text = persp_templates.get(perspective_key, persp_templates.get("third_person", ""))

        if perspective_key == "first_person":
            char_name = control.get("perspective_character", "主角")
            perspective_text = perspective_text.replace("{character}", char_name)

        style_key = control.get("style", "objective")
        style_templates = self.styles_config.get(lang, {})
        style_text = style_templates.get(style_key, style_templates.get("objective", ""))

        focus_key = control.get("narrative_focus", "general")
        focus_templates = self.query_templates.get(lang, {}).get("focus", {})
        focus_desc = focus_templates.get(focus_key, focus_templates.get("general", ""))
        narrative_focus_text = focus_desc.replace("{series_name}", series_name)

        # [新增] 总时长控制注入
        # 如果配置了 target_duration，我们将其追加到 narrative_focus 中，作为对整体长度的软约束
        target_duration_min = control.get("target_duration_minutes")
        if target_duration_min:
            narrative_focus_text += f"\n注意：请控制解说词的整体篇幅，使其对应的视频总时长约为 {target_duration_min} 分钟。"

        base_prompt_template = self._load_prompt_template(lang, "narration_generator")

        final_prompt = base_prompt_template.format(
            perspective=perspective_text,
            style=style_text,
            narrative_focus=narrative_focus_text,
            rag_context=enhanced_context
        )

        self.logger.info(f"Invoking Gemini [Style: {style_key}, Perspective: {perspective_key}]...")

        response_data, usage = self.gemini_processor.generate_content(
            model_name=config.get("model", "gemini-2.5-flash"),
            prompt=final_prompt,
            temperature=0.7
        )

        initial_script = response_data.get("narration_script", [])

        # --- Stage 4: Validation & Refinement (NEW) ---
        # 加载蓝图数据用于校验
        with blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        validator = NarrationValidator(blueprint_data, config, self.logger)
        refined_script = self._validate_and_refine(
            initial_script, validator, config, style_text, lang
        )

        return {
            "generation_date": datetime.now().isoformat(),
            "series_name": series_name,
            "config_snapshot": config,
            "narration_script": refined_script,
            "ai_total_usage": usage  # 注意：Refine 产生的 usage 暂时未合并进来，建议在 _validate_and_refine 中累加
        }

    def _validate_and_refine(self, script: List[Dict], validator: NarrationValidator,
                             config: Dict, style_text: str, lang: str) -> List[Dict]:
        """
        [Stage 4] 对生成的脚本进行逐条校验，如果超长则调用 AI 进行缩写。
        """
        self.logger.info(f"Starting Stage 4: Validation & Refinement for {len(script)} snippets...")
        final_script = []

        # [修改] 加载专门的 .txt 模板
        refine_template = self._load_prompt_template(lang, "narration_refine")

        for index, snippet in enumerate(script):
            is_valid, info = validator.validate_snippet(snippet)

            if is_valid:
                snippet["metadata"] = info
                final_script.append(snippet)
                continue

            self.logger.warning(f"Snippet {index} failed validation: {info}. Attempting refinement...")
            current_text = snippet["narration"]

            # 计算目标字数上限
            max_allowed_chars = int(info["real_visual_duration"] * validator.speaking_rate)

            # [修复] 初始化状态变量，防止 UnboundLocalError
            is_valid_now = False

            for attempt in range(self.MAX_REFINE_RETRIES):
                try:
                    # [修改] 使用 .format 填充 .txt 模板
                    refine_prompt = refine_template.format(
                        style=style_text,
                        original_text=current_text,
                        max_seconds=info["real_visual_duration"],
                        max_chars=max_allowed_chars
                    )

                    # 调用 AI (注意：Prompt 中已经包含了 JSON 格式要求)
                    refine_response_json, _ = self.gemini_processor.generate_content(
                        model_name=config.get("model", "gemini-2.5-flash"),
                        prompt=refine_prompt,
                        temperature=0.3
                    )

                    new_text = refine_response_json.get("refined_text", "")
                    if not new_text:
                        raise ValueError("Empty refinement result from LLM")

                    # 再次校验
                    snippet["narration"] = new_text
                    is_valid_now, new_info = validator.validate_snippet(snippet)

                    if is_valid_now:
                        self.logger.info(f"Snippet {index} refined successfully on attempt {attempt + 1}.")
                        snippet["metadata"] = new_info
                        snippet["metadata"]["refined"] = True
                        final_script.append(snippet)
                        break  # 成功，跳出重试循环
                    else:
                        self.logger.warning(
                            f"Refinement attempt {attempt + 1} failed: Still overflow ({new_info['overflow_sec']}s).")
                        current_text = new_text  # 用新的（虽然还是长的）文本继续尝试缩写
                        # 更新 info 供最后兜底使用
                        info = new_info

                except Exception as e:
                    self.logger.error(f"Error during refinement attempt {attempt + 1}: {e}")
                    # 异常发生时，继续下一次重试，或者退出循环
                    # 不要 break，给下一次重试机会

            # [逻辑闭环] 如果重试耗尽（或全报错）仍未成功
            if not is_valid_now:
                self.logger.error(
                    f"Snippet {index} failed refinement after {self.MAX_REFINE_RETRIES} retries (or errors). Keeping original/last version.")
                # 无论如何，保留当前的 text (可能是原始的，也可能是缩写了一半的)
                snippet["metadata"] = info
                snippet["metadata"]["validation_error"] = "Duration Overflow"
                # 标记虽然失败但尝试过
                if current_text != snippet["narration"]:
                    snippet["narration"] = current_text
                final_script.append(snippet)

        return final_script