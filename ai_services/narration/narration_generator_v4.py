# ai_services/narration/narration_generator_v3.py

import re  # [新增] 用于正则清洗
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

from pydantic import ValidationError

from ai_services.common.base_generator import BaseRagGenerator
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
from ai_services.rag.schemas import load_i18n_strings
from .schemas import NarrationServiceConfig, NarrationResult
from .query_builder import NarrationQueryBuilder
from .context_enhancer import ContextEnhancer
from .validator import NarrationValidator


class NarrationGeneratorV3(BaseRagGenerator):
    """
    [V3 Final] 智能解说词生成服务。
    Config-First 架构：asset_name 通过 Config 对象流转。
    资源管理：基于生命周期分离 (Query vs Prompt vs Refine)。
    能力增强：内置正则清洗，防止舞台指示泄露。
    """
    SERVICE_NAME = "narration_generator"
    MAX_REFINE_RETRIES = 2

    def __init__(self,
                 project_id: str,
                 location: str,
                 prompts_dir: Path,
                 metadata_dir: Path,
                 rag_schema_path: Path,
                 logger: logging.Logger,
                 work_dir: Path,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator):

        super().__init__(
            project_id=project_id,
            location=location,
            logger=logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator,
            prompts_dir=prompts_dir,
            metadata_dir=metadata_dir,
            work_dir=work_dir
        )

        load_i18n_strings(rag_schema_path)

        self.prompt_definitions = self._load_json_config("prompt_definitions.json")
        self.query_templates = self._load_json_config("query_templates.json")

        self.logger.info("NarrationGeneratorV3 initialized.")

    def _load_json_config(self, filename: str) -> Dict:
        path = self.metadata_dir / filename
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _sanitize_text(self, text: str) -> str:
        """
        [核心防御] 清洗文本，移除舞台指示和音效标记。
        目标：剔除 （音乐起）、(Laughs) 等括号内容。
        """
        if not text:
            return ""
        # 匹配中文括号 （...） 或 英文括号 (...)
        cleaned = re.sub(r'（.*?）|\(.*?\)', '', text)
        return cleaned.strip()

    def execute(self,
                asset_name: str,
                corpus_display_name: str,
                blueprint_path: Path,
                config: Dict[str, Any],
                asset_id: str) -> Dict[str, Any]:

        # 1. Pre-load blueprint for Top-K optimization
        if not blueprint_path.is_file():
            raise FileNotFoundError(f"Blueprint not found at: {blueprint_path}")

        try:
            with blueprint_path.open('r', encoding='utf-8') as f:
                bp_data = json.load(f)
            scenes = bp_data.get("scenes", {})
            total_scene_count = len(scenes)
            self.logger.info(f"Blueprint loaded. Total scenes found: {total_scene_count}")
        except Exception as e:
            self.logger.warning(f"Failed to pre-load blueprint: {e}. Using default.")
            total_scene_count = 9999

        # 2. Dynamic Top-K Adjustment
        requested_top_k = config.get('rag_top_k', 50)
        final_top_k = min(requested_top_k, total_scene_count)

        if final_top_k != requested_top_k:
            self.logger.info(f"Adjusting RAG Top-K: {requested_top_k} -> {final_top_k}")

        config_optimized = config.copy()
        config_optimized['rag_top_k'] = final_top_k
        config_optimized['asset_name'] = asset_name

        # 3. Invoke Base
        return super().execute(
            asset_name=asset_name,
            corpus_display_name=corpus_display_name,
            config=config_optimized,
            blueprint_path=blueprint_path,
            asset_id=asset_id
        )

    # --- Hooks Implementation ---

    def _validate_config(self, config: Dict[str, Any]) -> NarrationServiceConfig:
        try:
            return NarrationServiceConfig(**config)
        except ValidationError as e:
            self.logger.error(f"Config Validation Failed: {e}")
            raise ValueError(f"Invalid service parameters: {e}")

    def _build_query(self, config: NarrationServiceConfig) -> str:
        asset_name = config.asset_name or "Unknown Asset"
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        return qb.build(asset_name, config.dict())

    def _prepare_context(self, raw_chunks: List[Any], config: NarrationServiceConfig, **kwargs) -> str:
        blueprint_path = kwargs.get('blueprint_path')
        asset_id = kwargs.get('asset_id')
        enhancer = ContextEnhancer(blueprint_path, self.logger)
        return enhancer.enhance(raw_chunks, config.dict(), asset_id=asset_id)

    def _construct_prompt(self, context: str, config: NarrationServiceConfig) -> str:
        """Step 5: 组装 Prompt"""
        asset_name = config.asset_name or "Unknown Asset"
        lang = config.lang
        control = config.control_params

        # 加载语言包
        prompt_def = self.prompt_definitions.get(lang, self.prompt_definitions.get("en", {}))
        query_def = self.query_templates.get(lang, self.query_templates.get("en", {}))

        # 1. Perspective
        perspective_key = control.perspective
        perspectives = prompt_def.get("perspectives", {})
        perspective_text = perspectives.get(perspective_key, perspectives.get("third_person", ""))

        if perspective_key == "first_person":
            perspective_text = perspective_text.replace("{character}", control.perspective_character or "主角")

        # 2. Style (支持 Custom)
        style_key = control.style

        # [核心修改] 判断 Custom Style
        if style_key == "custom":
            # 这里的 control 是 Pydantic 对象，访问 custom_prompts 属性
            if control.custom_prompts and control.custom_prompts.style:
                style_text = control.custom_prompts.style
                self.logger.info(f"Using CUSTOM Style: {style_text[:30]}...")
            else:
                # 理论上 Validator 已拦截，但做个兜底
                style_text = "客观陈述"
        else:
            styles = prompt_def.get("styles", {})
            style_text = styles.get(style_key, styles.get("objective", ""))

        # 3. Focus
        focus_key = control.narrative_focus

        # [核心修改] 判断 Custom Focus (用于 Display/Prompt 描述，非检索)
        if focus_key == "custom":
            if control.custom_prompts and control.custom_prompts.narrative_focus:
                # 注意：RAG Query 已经用了这个，这里是为了填入 Prompt 里的 {narrative_focus} 槽位
                # 告诉 LLM “我要讲什么故事”
                narrative_focus_text = control.custom_prompts.narrative_focus
                # 替换可能存在的占位符
                narrative_focus_text = narrative_focus_text.replace("{asset_name}", asset_name)
            else:
                narrative_focus_text = f"关于 {asset_name} 的故事"
        else:
            focus_templates = query_def.get("focus", {})
            focus_desc = focus_templates.get(focus_key, focus_templates.get("general", ""))
            narrative_focus_text = focus_desc.replace("{asset_name}", asset_name)

        # 4. Constraints
        if control.target_duration_minutes:
            constraints = prompt_def.get("constraints", {})
            duration_tpl = constraints.get("duration_guideline", "")
            if duration_tpl:
                duration_text = duration_tpl.format(minutes=control.target_duration_minutes)

                target_chars = int(control.target_duration_minutes * 60 * config.speaking_rate)
                char_tpl = constraints.get("char_limit_instruction", "")
                char_constraint = char_tpl.format(target_chars=target_chars)

                narrative_focus_text += "\n" + duration_text + char_constraint

        # 5. Template
        base_template = self._load_prompt_template(lang, "narration_generator")
        return base_template.format(
            perspective=perspective_text,
            style=style_text,
            narrative_focus=narrative_focus_text,
            rag_context=context
        )

    def _post_process(self, llm_response: Dict, config: NarrationServiceConfig, usage: Dict, **kwargs) -> Dict:
        """Step 7: 后处理 (翻译 -> 清洗 -> 校验 -> 缩写 -> 导演 -> 封装)"""
        initial_script = llm_response.get("narration_script", [])
        blueprint_path = kwargs.get('blueprint_path')
        asset_name = config.asset_name or "Unknown Asset"

        # [新增] 获取基类传来的上下文
        rag_context = kwargs.get('rag_context', '')

        # ==========================================
        # 1. [新增] 翻译环节 (Translate Layer)
        # ==========================================
        # 逻辑：如果有 target_lang 且与源语言不同，则触发
        if config.target_lang and config.target_lang != config.lang:
            self.logger.info(f"Starting Context-Aware Translation: {config.lang} -> {config.target_lang}")
            initial_script = self._translate_script(
                initial_script, config.lang, config.target_lang, rag_context, config.model
            )
            processing_lang = config.target_lang

        # ==========================================
        # 2. 预清洗 (Sanitize)
        # ==========================================
        # 此时 script 已经是目标语言了
        for item in initial_script:
            if "narration" in item:
                item["narration"] = self._sanitize_text(item["narration"])

        # ==========================================
        # 3. 校验与缩写 (Validation & Refine Loop)
        # ==========================================
        with blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        validation_lang = config.target_lang if (
                    config.target_lang and config.target_lang != config.lang) else config.lang

        validator = NarrationValidator(blueprint_data, config.dict(), self.logger, lang=validation_lang)
        #validator = NarrationValidator(blueprint_data, config.dict(), self.logger)
        # 注意：这里会对“翻译后”的文本进行语速和时长校验，这是完全正确的逻辑
        final_script_list = self._validate_and_refine(initial_script, validator, config, processing_lang)

        # ==========================================
        # 4. 配音导演 (Audio Directing)
        # ==========================================
        if final_script_list:
            self.logger.info("Starting Stage 5: Audio Directing...")
            final_script_list = self._enrich_audio_directives(final_script_list, config, processing_lang)

        # ==========================================
        # 5. 封装结果
        # ==========================================
        # 获取上下文 (由基类传入)
        rag_context = kwargs.get('rag_context', '')

        try:
            result = NarrationResult(
                generation_date=datetime.now().isoformat(),
                asset_name=asset_name,
                source_corpus=kwargs.get('corpus_display_name', 'unknown'),
                rag_context_snapshot=rag_context,
                narration_script=final_script_list,
                ai_total_usage=usage
            )
            return result.dict()
        except ValidationError as e:
            self.logger.error(f"Output Validation Failed: {e}")
            raise RuntimeError(f"Generated result failed schema validation: {e}")

    def _translate_script(self, script: List[Dict], src_lang: str, tgt_lang: str, context: str, model: str) -> List[Dict]:
        """
        [Stage 3.5] 上下文感知翻译器。
        """
        if not script: return []

        # 简化输入，节省 Token
        simplified_input = [
            {"index": i, "narration": item["narration"]}
            for i, item in enumerate(script)
        ]

        # [关键] 这里的 lang 参数决定了加载 translator_zh.txt 还是 translator_en.txt
        # 如果您的系统主要服务中文用户，或者 config.lang 是 'zh'，则加载 'zh'
        # 建议直接使用 self.config.lang (即服务的主语言)
        # 比如：服务设为 zh，那么即使是从 en 翻译到 es，我们也用中文给 LLM 下指令
        instruction_lang = self.config.lang if hasattr(self, 'config') else 'zh'

        translator_template = self._load_prompt_template(instruction_lang, "narration_translator")

        prompt = translator_template.format(
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            rag_context=context,  # [核心] 注入 RAG 上下文，解决代词歧义
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        try:
            self.logger.info("Invoking LLM for Context-Aware Translation...")
            response_data, _ = self.gemini_processor.generate_content(
                model_name=model,
                prompt=prompt,
                temperature=0.3  # 翻译追求准确
            )

            translated_data = response_data.get("translated_script", [])

            # 回填翻译结果 (使用 map 加速)
            trans_map = {item["index"]: item["narration"] for item in translated_data}

            for i, item in enumerate(script):
                if i in trans_map:
                    # [核心修改] 备份源文本 (Source)
                    # 如果 script 是从上游传来的，可能已经有 narration 了
                    item["narration_source"] = item["narration"]

                    # 更新为主文本 (Target)
                    item["narration"] = trans_map[i]

                    # 清空旧的 metadata (因为时长、缩写状态都变了，需要重新 Refine)
                    if "metadata" in item:
                        # 保留部分基础信息，清除时长相关
                        item["metadata"].pop("refined", None)
                        item["metadata"].pop("overflow_sec", None)

            self.logger.info(f"Successfully translated {len(translated_data)} clips.")

        except Exception as e:
            self.logger.error(f"Translation failed: {e}. Falling back to source language.")
            # 降级策略：保持原文，不抛错

        return script

    def _validate_and_refine(self, script: List[Dict], validator: NarrationValidator,
                             config: NarrationServiceConfig, lang: str) -> List[Dict]:
        self.logger.info(f"Starting Stage 4: Validation & Refinement...")
        final_script = []
        #lang = config.lang
        style_key = config.control_params.style

        # [核心修改] 获取 Refine 用的 Style 描述
        if style_key == "custom":
            if config.control_params.custom_prompts:
                style_text = config.control_params.custom_prompts.style
            else:
                style_text = "客观"
        else:
            prompt_def = self.prompt_definitions.get(lang, self.prompt_definitions.get("en", {}))
            style_text = prompt_def.get("styles", {}).get(style_key, "")

        refine_template = self._load_prompt_template(lang, "narration_refine")

        for index, snippet in enumerate(script):
            is_valid, info = validator.validate_snippet(snippet)
            if is_valid:
                snippet["metadata"] = info
                final_script.append(snippet)
                continue

            self.logger.warning(f"Snippet {index} failed validation. Attempting refinement...")
            current_text = snippet.get("narration", "")
            max_allowed_chars = int(info["real_visual_duration"] * validator.speaking_rate)
            is_valid_now = False

            for attempt in range(self.MAX_REFINE_RETRIES):
                try:
                    refine_prompt = refine_template.format(
                        style=style_text,
                        original_text=current_text,
                        max_seconds=info["real_visual_duration"],
                        max_chars=max_allowed_chars
                    )
                    refine_response, _ = self.gemini_processor.generate_content(
                        model_name=config.model,
                        prompt=refine_prompt,
                        temperature=0.3
                    )
                    new_text = refine_response.get("refined_text", "")
                    if not new_text: continue

                    # [核心防御] 2. 清洗缩写后的文本：防止 Refine 过程中引入新的舞台指示
                    new_text = self._sanitize_text(new_text)

                    snippet["narration"] = new_text
                    is_valid_now, new_info = validator.validate_snippet(snippet)
                    if is_valid_now:
                        snippet["metadata"] = new_info
                        snippet["metadata"]["refined"] = True
                        final_script.append(snippet)
                        break
                    else:
                        current_text = new_text
                        info = new_info
                except Exception:
                    pass

            if not is_valid_now:
                snippet["metadata"] = info
                snippet["metadata"]["validation_error"] = "Duration Overflow"
                if current_text != snippet.get("narration"):
                    snippet["narration"] = current_text
                final_script.append(snippet)

        return final_script

    def _enrich_audio_directives(self, script: List[Dict], config: NarrationServiceConfig, lang: str) -> List[Dict]:
        """
        [Stage 6] 配音导演模式。
        批量将定稿的 script 发送给 LLM，请求生成 tts_instruct 和 narration_for_audio。
        """
        #lang = config.lang
        # 准备发给 LLM 的简化版 JSON (只包含文本，节省 Token)
        simplified_input = [
            {"index": i, "narration": item["narration"]}
            for i, item in enumerate(script)
        ]

        # 获取 Style 描述
        if config.control_params.style == "custom" and config.control_params.custom_prompts:
            style_text = config.control_params.custom_prompts.style
        else:
            prompt_def = self.prompt_definitions.get(lang, self.prompt_definitions.get("en", {}))
            style_text = prompt_def.get("styles", {}).get(config.control_params.style, "")

        # 获取 Perspective 描述
        prompt_def = self.prompt_definitions.get(lang, self.prompt_definitions.get("en", {}))
        perspective_text = prompt_def.get("perspectives", {}).get(config.control_params.perspective, "")

        # 加载模版 (请确保 narration_audio_director_zh.txt 已创建)
        director_template = self._load_prompt_template(lang, "narration_audio_director")

        prompt = director_template.format(
            style=style_text,
            perspective=perspective_text,
            script_json=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )

        try:
            # 调用 LLM (Batch 处理所有片段)
            self.logger.info("Invoking Audio Director for TTS instructions...")
            response_data, _ = self.gemini_processor.generate_content(
                model_name=config.model,
                prompt=prompt,
                temperature=0.7  # 导演需要一点创造力
            )

            enriched_data = response_data.get("enriched_script", [])

            # 将结果回填到原 script 列表
            # 使用 index 匹配，防止顺序错乱
            enrich_map = {item["index"]: item for item in enriched_data}

            for i, item in enumerate(script):
                directive = enrich_map.get(i)
                if directive:
                    item["tts_instruct"] = directive.get("tts_instruct")
                    item["narration_for_audio"] = directive.get("narration_for_audio")
                else:
                    # 兜底：如果没有生成指令，就用默认值
                    item["tts_instruct"] = "Speak naturally."
                    # 如果没有 narration_for_audio，下游 DubbingEngine 会自动回退到 narration，这里可以不填
                    # item["narration_for_audio"] = item["narration"]

            self.logger.info(f"Successfully enriched {len(enriched_data)} clips with audio directives.")

        except Exception as e:
            self.logger.error(f"Audio Director failed: {e}. Falling back to raw narration.")
            # 降级策略：如果导演环节挂了，不要让整个任务失败，保留原文本即可
            pass

        return script

    def execute_localization(self,
                             master_script_data: Dict[str, Any],
                             config: Dict[str, Any]) -> Dict[str, Any]:
        """
        [独立入口] 执行本地化任务：读取母本 -> 翻译 -> 校验/缩写 -> 导演
        """
        self.logger.info(f"Starting Localization Task for: {master_script_data.get('asset_name')}")

        # 1. 校验配置
        # 注意：这里不需要 blueprint_path，因为我们只做纯文本处理
        # 但我们需要 speaking_rate 等参数，所以依然用 NarrationServiceConfig 校验
        try:
            service_config = NarrationServiceConfig(**config)
        except ValidationError as e:
            raise ValueError(f"Invalid service parameters: {e}")

        # 2. 提取上下文
        # 关键：从母本结果中恢复 RAG Context，无需重新检索！
        rag_context = master_script_data.get("rag_context_snapshot", "")
        if not rag_context:
            self.logger.warning("Master script missing 'rag_context_snapshot'. Translation accuracy may drop.")

        # 3. 提取脚本
        # 注意：母本里的 narration_script 是一个 List[Dict]，需要适配
        # Pydantic model dump 出来的可能是 dict，也可能是 list of objects
        input_script = master_script_data.get("narration_script", [])

        # 4. 复用 _post_process 的逻辑链
        # 我们构造一个伪造的 llm_response，骗过 _post_process
        # 或者更干净的做法：把 _post_process 里的逻辑拆出来。
        # 为了代码复用最大化，我们直接复用 _post_process，因为它正好就是干这个的。

        # 唯一的特殊点：_post_process 需要 blueprint_path 来做 Validator
        # 这里我们面临一个抉择：
        # A. 本地化任务也必须传 blueprint (为了获取 accurately visual duration) -> **推荐，最精准**
        # B. 本地化任务直接信赖母本里的 duration (如果母本已经对齐了) -> 不行，因为翻译后时长变了，必须重新校验

        # 结论：本地化任务也需要 blueprint_path 来计算物理时长限制

        blueprint_path = Path(config.get("blueprint_path", ""))  # 假设调用方会传
        if not blueprint_path.is_file():
            raise ValueError("Localization requires 'blueprint_path' to validate timing.")

        # 调用核心流水线
        # 此时 config.lang 是源语言，config.target_lang 是目标语言
        # usage 传一个空的，慢慢累加
        usage = {"cost_usd": 0.0, "total_tokens": 0}

        final_result = self._post_process(
            llm_response={"narration_script": input_script},
            config=service_config,
            usage=usage,
            corpus_display_name=master_script_data.get("source_corpus", "unknown"),
            rag_context=rag_context,
            blueprint_path=blueprint_path,  # 传入路径
            asset_name=master_script_data.get("asset_name")
        )

        return final_result