# ai_services/narration/narration_generator_v2.py

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import vertexai
from vertexai import rag

# 基础组件
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.rag.schemas import load_i18n_strings

# V2 核心组件
from .query_builder import NarrationQueryBuilder
from .context_enhancer import ContextEnhancer
from .validator import NarrationValidator


class NarrationGeneratorV2(AIServiceMixin):
    """
    [V2] 智能解说词生成服务。

    架构特点:
        采用 "Query -> Enhance -> Synthesize -> Refine" 四段式流水线。
        支持有目的检索、本地上下文增强、风格化生成以及基于时长的自动缩写校验。
    """
    SERVICE_NAME = "narration_generator"

    # 最大重试缩写次数，防止死循环
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

        # 初始化 Vertex AI
        vertexai.init(project=self.project_id, location=self.location)

        # 加载全局资源
        load_i18n_strings(rag_schema_path)
        self.styles_config = self._load_json_config("styles.json")
        self.perspectives_config = self._load_json_config("perspectives.json")
        self.query_templates = self._load_json_config("query_templates.json")

        self.logger.info("NarrationGeneratorV2 initialized (Full Configuration).")

    def _load_json_config(self, filename: str) -> Dict:
        """加载 metadata 目录下的 JSON 配置文件"""
        path = self.metadata_dir / filename
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        """根据显示名称获取 RAG Corpus 引用"""
        corpora = rag.list_corpora()
        corpus = next((c for c in corpora if c.display_name == corpus_display_name), None)
        if not corpus:
            raise RuntimeError(f"RAG Corpus not found: '{corpus_display_name}'")
        return corpus

    def execute(self,
                asset_name: str,
                corpus_display_name: str,
                blueprint_path: Path,
                config: Dict[str, Any],
                asset_id: str) -> Dict[str, Any]:
        """
        执行 V2 生成流程主入口。
        """
        self.logger.info(f"Starting V2 Generation for: {asset_name} (Asset: {asset_id})")

        # ==================================================================
        # Stage 1: Intent-Based Retrieval (有目的检索)
        # ==================================================================
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        query = qb.build(asset_name, config)

        rag_corpus = self._get_rag_corpus(corpus_display_name)
        top_k = config.get("rag_top_k", 50)

        self.logger.info(f"Retrieving from RAG (top_k={top_k})...")
        retrieval_response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
            text=query,
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
        )
        raw_chunks = retrieval_response.contexts.contexts

        # ==================================================================
        # Stage 2: Context Enhancement (本地时序增强)
        # ==================================================================
        enhancer = ContextEnhancer(blueprint_path, self.logger)
        enhanced_context = enhancer.enhance(raw_chunks, config, asset_id=asset_id)

        if not enhanced_context or "No relevant scenes" in enhanced_context:
            self.logger.warning("Context enhancement resulted in empty content.")
            return {"narration_script": [], "metadata": {"status": "empty_context"}}

        # ==================================================================
        # Stage 3: Synthesis (风格化合成)
        # ==================================================================
        lang = config.get("lang", "zh")
        control = config.get("control_params", {})

        # 3.1 组装 Perspective (视角)
        perspective_key = control.get("perspective", "third_person")
        persp_templates = self.perspectives_config.get(lang, self.perspectives_config.get("en", {}))
        perspective_text = persp_templates.get(perspective_key, persp_templates.get("third_person", ""))

        if perspective_key == "first_person":
            char_name = control.get("perspective_character", "主角")
            perspective_text = perspective_text.replace("{character}", char_name)

        # 3.2 组装 Style (风格)
        style_key = control.get("style", "objective")
        style_templates = self.styles_config.get(lang, self.styles_config.get("en", {}))
        style_text = style_templates.get(style_key, style_templates.get("objective", ""))

        # 3.3 组装 Narrative Focus (叙事目标)
        focus_key = control.get("narrative_focus", "general")
        query_lang_pack = self.query_templates.get(lang, self.query_templates.get("en", {}))
        focus_templates = query_lang_pack.get("focus", {})
        focus_desc = focus_templates.get(focus_key, focus_templates.get("general", ""))
        narrative_focus_text = focus_desc.replace("{asset_name}", asset_name)

        # [注入] 总时长控制 (Duration Constraint)
        target_duration_min = control.get("target_duration_minutes")
        if target_duration_min:
            duration_tpl = query_lang_pack.get("duration_constraint", "")
            if duration_tpl:
                narrative_focus_text += "\n" + duration_tpl.format(minutes=target_duration_min)

        # 3.4 组装最终 Prompt
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

        # ==================================================================
        # Stage 4: Validation & Refinement (校验与精调)
        # ==================================================================
        with blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        validator = NarrationValidator(blueprint_data, config, self.logger)

        # 执行校验循环
        refined_script = self._validate_and_refine(
            initial_script, validator, config, style_text, lang
        )

        return {
            "generation_date": datetime.now().isoformat(),
            "asset_name": asset_name,
            "config_snapshot": config,
            "narration_script": refined_script,
            "ai_total_usage": usage
        }

    def _validate_and_refine(self, script: List[Dict], validator: NarrationValidator,
                             config: Dict, style_text: str, lang: str) -> List[Dict]:
        """
        逐条校验生成的脚本，若时长溢出则调用 AI 进行缩写。
        """
        self.logger.info(f"Starting Stage 4: Validation & Refinement for {len(script)} snippets...")
        final_script = []

        # 加载缩写专用的 .txt 模版
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

            is_valid_now = False

            # 进入重试循环
            for attempt in range(self.MAX_REFINE_RETRIES):
                try:
                    refine_prompt = refine_template.format(
                        style=style_text,
                        original_text=current_text,
                        max_seconds=info["real_visual_duration"],
                        max_chars=max_allowed_chars
                    )

                    # 调用 AI (Prompt 中已包含 JSON 格式要求)
                    refine_response_json, _ = self.gemini_processor.generate_content(
                        model_name=config.get("model", "gemini-2.5-flash"),
                        prompt=refine_prompt,
                        temperature=0.3  # 低温以确保遵循约束
                    )

                    new_text = refine_response_json.get("refined_text", "")
                    if not new_text:
                        raise ValueError("Empty refinement result from LLM")

                    # 再次校验新文本
                    snippet["narration"] = new_text
                    is_valid_now, new_info = validator.validate_snippet(snippet)

                    if is_valid_now:
                        self.logger.info(f"Snippet {index} refined successfully on attempt {attempt + 1}.")
                        snippet["metadata"] = new_info
                        snippet["metadata"]["refined"] = True
                        final_script.append(snippet)
                        break
                    else:
                        self.logger.warning(
                            f"Refinement attempt {attempt + 1} failed: Still overflow ({new_info['overflow_sec']}s).")
                        # 更新状态，准备下一次重试
                        current_text = new_text
                        info = new_info

                except Exception as e:
                    self.logger.error(f"Error during refinement attempt {attempt + 1}: {e}")

            # 如果重试耗尽仍未通过，保留最后一次结果并标记错误
            if not is_valid_now:
                self.logger.error(
                    f"Snippet {index} failed refinement after {self.MAX_REFINE_RETRIES} retries. Keeping last version.")
                snippet["metadata"] = info
                snippet["metadata"]["validation_error"] = "Duration Overflow"
                # 确保 narration 字段存的是最后一次尝试的文本
                if current_text != snippet["narration"]:
                    snippet["narration"] = current_text
                final_script.append(snippet)

        return final_script