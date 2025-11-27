# ai_services/narration/narration_generator.py

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple

import vertexai
from vertexai import rag
from pydantic import ValidationError

# 基础组件
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor

# 引入 RAG 蓝图 Schema 用于输入校验
from ai_services.rag.schemas import load_i18n_strings

# [New] 引入数据契约 (请确保 ai_services/narration/schemas.py 已创建)
from .schemas import NarrationServiceConfig, NarrationResult

# V2 核心组件 (重命名后文件名应已去除 _v2 后缀，或者保持原引用，此处假设同级目录)
from .query_builder import NarrationQueryBuilder
from .context_enhancer import ContextEnhancer
from .validator import NarrationValidator


class NarrationGenerator(AIServiceMixin):
    """
    [V2 Refactored] 智能解说词生成服务。

    架构特点:
        采用 "Query -> Enhance -> Synthesize -> Refine" 四段式流水线。
        集成了 Pydantic 数据契约，支持有目的检索、本地上下文增强、风格化生成以及自动缩写校验。
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
        try:
            vertexai.init(project=self.project_id, location=self.location)
        except Exception as e:
            self.logger.error(f"Vertex AI initialization failed: {e}")
            raise

        # 加载全局资源
        load_i18n_strings(rag_schema_path)
        self.styles_config = self._load_json_config("styles.json")
        self.perspectives_config = self._load_json_config("perspectives.json")
        self.query_templates = self._load_json_config("query_templates.json")

        self.logger.info("NarrationGenerator initialized (Hardened Version).")

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
        执行生成流程主入口。
        """
        self.logger.info(f"Starting Narration Generation for: {asset_name} (Asset: {asset_id})")

        # ==================================================================
        # Step 1: 强类型参数校验 (Input Validation)
        # ==================================================================
        try:
            # 将字典转换为 Pydantic 模型，自动过滤非法字段并补全默认值
            service_config = NarrationServiceConfig(**config)

            if not blueprint_path.is_file():
                raise FileNotFoundError(f"Blueprint not found at: {blueprint_path}")

        except ValidationError as e:
            self.logger.error(f"Configuration Validation Failed: {e}")
            raise ValueError(f"Invalid service parameters: {e}")

        # ==================================================================
        # Step 2: 有目的检索 (Intent-Based Retrieval)
        # ==================================================================
        # 注意: QueryBuilder 目前仍接受 dict，所以我们传 service_config.dict()
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        query = qb.build(asset_name, service_config.dict())

        rag_corpus = self._get_rag_corpus(corpus_display_name)
        top_k = service_config.rag_top_k

        self.logger.info(f"Retrieving from RAG (top_k={top_k})...")
        try:
            retrieval_response = rag.retrieval_query(
                rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)],
                text=query,
                rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
            )
            raw_chunks = retrieval_response.contexts.contexts
        except Exception as e:
            self.logger.error(f"RAG Retrieval failed: {e}")
            raise RuntimeError(f"RAG Service Error: {e}")

        if not raw_chunks:
            # [防御] RAG 返回空，直接报错，不要往下走了
            raise RuntimeError(f"RAG retrieval returned 0 chunks for query: '{query}'. Check Asset ID mismatch.")

        # ==================================================================
        # Step 3: 上下文增强 (Context Enhancement)
        # ==================================================================
        enhancer = ContextEnhancer(blueprint_path, self.logger)
        enhanced_context = enhancer.enhance(raw_chunks, service_config.dict(), asset_id=asset_id)

        if not enhanced_context or "No relevant scenes" in enhanced_context:
            # [防御] 增强后为空，说明 Scope 过滤太严格或 RAG 结果完全不相关
            raise RuntimeError("Context enhancement resulted in empty content. Please check 'scope' parameters.")

        # ==================================================================
        # Step 4: 风格化合成 (Synthesis)
        # ==================================================================
        initial_script, usage = self._synthesize_script(
            asset_name, enhanced_context, service_config
        )

        # ==================================================================
        # Step 5: 校验与精调 (Validation & Refinement)
        # ==================================================================
        with blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        # Validator 需要原始蓝图数据来计算物理时长
        # 注意：Validator 内部可能还在用 config.get()，所以传 dict 比较安全
        validator = NarrationValidator(blueprint_data, service_config.dict(), self.logger)

        # 执行校验循环
        final_script_list = self._validate_and_refine(
            initial_script, validator, service_config
        )

        # ==================================================================
        # Step 6: 强类型结果校验 (Output Validation)
        # ==================================================================
        try:
            result = NarrationResult(
                generation_date=datetime.now().isoformat(),
                asset_name=asset_name,
                source_corpus=corpus_display_name,
                narration_script=final_script_list,
                ai_total_usage=usage
            )
            self.logger.info(f"Successfully generated {len(result.narration_script)} narration snippets.")
            return result.dict()
        except ValidationError as e:
            self.logger.error(f"Output Validation Failed: {e}")
            raise RuntimeError(f"Generated result failed schema validation: {e}")

    def _construct_final_prompt(self, asset_name: str, context: str, config: NarrationServiceConfig) -> str:
        """
        组装生成解说词的完整 Prompt。
        """
        lang = config.lang
        control = config.control_params

        # 1. Perspective (视角)
        perspective_key = control.perspective
        persp_templates = self.perspectives_config.get(lang, self.perspectives_config.get("en", {}))
        perspective_text = persp_templates.get(perspective_key, persp_templates.get("third_person", ""))

        if perspective_key == "first_person":
            char_name = control.perspective_character or "主角"
            perspective_text = perspective_text.replace("{character}", char_name)

        # 2. Style (风格)
        style_key = control.style
        style_templates = self.styles_config.get(lang, self.styles_config.get("en", {}))
        style_text = style_templates.get(style_key, style_templates.get("objective", ""))

        # 3. Narrative Focus (叙事目标)
        focus_key = control.narrative_focus
        query_lang_pack = self.query_templates.get(lang, self.query_templates.get("en", {}))
        focus_templates = query_lang_pack.get("focus", {})
        focus_desc = focus_templates.get(focus_key, focus_templates.get("general", ""))
        narrative_focus_text = focus_desc.replace("{asset_name}", asset_name)

        # [注入] 总时长控制 (Duration Constraint)
        target_duration_min = control.target_duration_minutes
        if target_duration_min:
            duration_tpl = query_lang_pack.get("duration_constraint", "")
            if duration_tpl:
                narrative_focus_text += "\n" + duration_tpl.format(minutes=target_duration_min)

        # 4. 组装最终 Prompt
        base_prompt_template = self._load_prompt_template(lang, "narration_generator")

        return base_prompt_template.format(
            perspective=perspective_text,
            style=style_text,
            narrative_focus=narrative_focus_text,
            rag_context=context
        )

    def _synthesize_script(self, asset_name: str, context: str, config: NarrationServiceConfig) -> Tuple[
        List[Dict], Dict]:
        """
        执行初次合成逻辑。
        """
        final_prompt = self._construct_final_prompt(asset_name, context, config)
        control = config.control_params

        self.logger.info(f"Invoking Gemini [Style: {control.style}, Perspective: {control.perspective}]...")

        response_data, usage = self.gemini_processor.generate_content(
            model_name=config.model,
            prompt=final_prompt,
            temperature=0.7
        )

        initial_script = response_data.get("narration_script", [])
        return initial_script, usage

    def _validate_and_refine(self, script: List[Dict], validator: NarrationValidator,
                             config: NarrationServiceConfig) -> List[Dict]:
        """
        逐条校验生成的脚本，若时长溢出则调用 AI 进行缩写。
        """
        self.logger.info(f"Starting Stage 4: Validation & Refinement for {len(script)} snippets...")
        final_script = []

        lang = config.lang
        style_key = config.control_params.style

        # 获取 Style 描述文本用于 Refine Prompt
        style_templates = self.styles_config.get(lang, self.styles_config.get("en", {}))
        style_text = style_templates.get(style_key, "")

        # 加载缩写专用的 .txt 模版
        refine_template = self._load_prompt_template(lang, "narration_refine")

        for index, snippet in enumerate(script):
            is_valid, info = validator.validate_snippet(snippet)

            if is_valid:
                snippet["metadata"] = info
                final_script.append(snippet)
                continue

            self.logger.warning(f"Snippet {index} failed validation: {info}. Attempting refinement...")
            current_text = snippet.get("narration", "")

            # 计算目标字数上限
            # 注意：validator.speaking_rate 应该能被正确访问
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
                        model_name=config.model,
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
                            f"Refinement attempt {attempt + 1} failed: Still overflow ({new_info['overflow_sec']}s)."
                        )
                        # 更新状态，准备下一次重试
                        current_text = new_text
                        info = new_info

                except Exception as e:
                    self.logger.error(f"Error during refinement attempt {attempt + 1}: {e}")

            # 如果重试耗尽仍未通过，保留最后一次结果并标记错误
            if not is_valid_now:
                self.logger.error(
                    f"Snippet {index} failed refinement after {self.MAX_REFINE_RETRIES} retries. Keeping last version."
                )
                snippet["metadata"] = info
                snippet["metadata"]["validation_error"] = "Duration Overflow"
                # 确保 narration 字段存的是最后一次尝试的文本
                if current_text != snippet.get("narration"):
                    snippet["narration"] = current_text
                final_script.append(snippet)

        return final_script