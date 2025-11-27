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
        """Step 7: 后处理 (清洗、校验、缩写、结果封装)"""
        initial_script = llm_response.get("narration_script", [])
        blueprint_path = kwargs.get('blueprint_path')
        asset_name = config.asset_name or "Unknown Asset"

        # [核心防御] 1. 预清洗初稿：移除 LLM 可能输出的舞台指示
        for item in initial_script:
            if "narration" in item:
                item["narration"] = self._sanitize_text(item["narration"])

        # 加载蓝图数据用于 Validator
        with blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        validator = NarrationValidator(blueprint_data, config.dict(), self.logger)
        final_script_list = self._validate_and_refine(initial_script, validator, config)

        try:
            result = NarrationResult(
                generation_date=datetime.now().isoformat(),
                asset_name=asset_name,
                source_corpus=kwargs.get('corpus_display_name', 'unknown'),
                narration_script=final_script_list,
                ai_total_usage=usage
            )
            return result.dict()
        except ValidationError as e:
            self.logger.error(f"Output Validation Failed: {e}")
            raise RuntimeError(f"Generated result failed schema validation: {e}")

    def _validate_and_refine(self, script: List[Dict], validator: NarrationValidator,
                             config: NarrationServiceConfig) -> List[Dict]:
        self.logger.info(f"Starting Stage 4: Validation & Refinement...")
        final_script = []
        lang = config.lang
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