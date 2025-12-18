import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

from pydantic import ValidationError

from ai_services.ai_platform.llm.base_generator import BaseRagGenerator
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator

from ai_services.biz_services.narration.schemas import NarrationServiceConfig, NarrationResult, NarrationSnippet
from ai_services.biz_services.narrative_dataset import NarrativeDataset

from .components.query_builder import NarrationQueryBuilder
from .components.context_enhancer import ContextEnhancer
from .components.pacing_checker import NarrationPacingChecker
from .components.utils import sanitize_text

from ai_services.ai_core_units.text_refiner.refiner import TextRefiner



class NarrationGenerator(BaseRagGenerator):
    """
    [Service Layer] Narration Generator V5 (Type Safe).
    """

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

        self.metadata_dir = metadata_dir
        # 加载内部配置
        self.prompt_definitions = self._load_internal_config("prompt_definitions.json")
        self.query_templates = self._load_internal_config("query_templates.json")

    def _load_internal_config(self, filename: str) -> Dict:
        path = self.metadata_dir / filename
        if path.is_file():
            with path.open(encoding="utf-8") as f: return json.load(f)
        return {}

    # --- 核心 Hook 实现 ---

    def _validate_config(self, config: Dict[str, Any]) -> NarrationServiceConfig:
        """Step 1: 将输入 Dict 转化为强类型 Config 对象"""
        try:
            # NarrativeDataset 实例化检查
            if "narrative_dataset" in config and isinstance(config["narrative_dataset"], dict):
                config["narrative_dataset"] = NarrativeDataset(**config["narrative_dataset"])

            return NarrationServiceConfig(**config)
        except ValidationError as e:
            self.logger.error(f"Config Validation Failed: {e}")
            raise ValueError(f"Invalid service configuration: {e}")

    def _build_query(self, config: NarrationServiceConfig) -> str:
        """Step 2: 构建 RAG 查询"""
        qb = NarrationQueryBuilder(self.metadata_dir, self.logger)
        # [Refactor] 传入 Config 对象，不再 dump
        return qb.build(config)  # asset_name 已包含在 config 中

    def _prepare_context(self, raw_chunks: List[Any], config: NarrationServiceConfig, **kwargs) -> str:
        """Step 4: 增强上下文"""
        if not config.narrative_dataset:
            raise ValueError("Fatal: NarrativeDataset is missing in config!")

        # [Fix] 传入 prompt_definitions 以支持 i18n 模版
        enhancer = ContextEnhancer(
            dataset=config.narrative_dataset,
            prompt_definitions=self.prompt_definitions,  # 新增传参
            logger=self.logger
        )
        return enhancer.enhance(raw_chunks, config)

    def _construct_prompt(self, context: str, config: NarrationServiceConfig) -> str:
        """Step 5: 组装 Prompt"""
        return self._assemble_prompt_string(context, config)

    def _post_process(self, llm_response: Dict, config: NarrationServiceConfig, usage: Dict, **kwargs) -> Dict:
        """Step 7: 后处理"""
        dataset = config.narrative_dataset
        lang = config.lang

        # 1. 初始化 PacingChecker (传入 Dataset 对象)
        pacing_checker = NarrationPacingChecker(dataset, config, self.logger)

        # 2. 初始化 TextRefiner (通用单元)
        refiner = TextRefiner(self.gemini_processor)

        # 3. 准备提示词模版
        refine_prompt_template = refiner.load_template("narration_refine",lang)
        style_desc = self._resolve_prompt_content(lang, "styles", config.control_params.style,
                                                  config.control_params.custom_prompts)

        initial_script = llm_response.get("narration_script", [])
        final_script = []

        # 4. 循环处理
        for index, snippet in enumerate(initial_script):
            # Sanitize first
            if "narration" in snippet:
                snippet["narration"] = sanitize_text(snippet["narration"])

            # A. 检查步调 (Check Pacing)
            is_ok, info = pacing_checker.check_pacing(snippet)

            # 如果 OK 或者 视觉时长异常(0.0)，则直接通过
            if is_ok or info['real_visual_duration'] <= 0.1:
                snippet["metadata"] = info
                final_script.append(snippet)
                continue

            # B. 溢出处理 (Refine Loop)
            self.logger.warning(f"Snippet {index} overflow ({info['overflow_sec']}s). Calling TextRefiner...")

            # 计算 Refiner 需要的参数
            safe_max_chars = max(10, int(info["real_visual_duration"] * pacing_checker.speaking_rate))

            refined_text = refiner.refine_content(
                content=snippet["narration"],
                prompt_template=refine_prompt_template,
                model_name=config.model,
                # kwargs for template
                style=style_desc,
                max_seconds=info["real_visual_duration"],
                max_chars=safe_max_chars
            )

            if refined_text:
                snippet["narration"] = refined_text
                # 复检
                is_ok_now, new_info = pacing_checker.check_pacing(snippet)
                snippet["metadata"] = new_info
                snippet["metadata"]["refined"] = True
                if not is_ok_now:
                    snippet["metadata"]["validation_error"] = "Still Overflow after Refine"
            else:
                # Refine 失败，保留原样并标记
                snippet["metadata"] = info
                snippet["metadata"]["validation_error"] = "Refine Failed"

            final_script.append(snippet)

        # 5. Result Packaging
        script_objects = [NarrationSnippet(**item) for item in final_script]

        result = NarrationResult(
            generation_date=datetime.now().isoformat(),
            asset_name=config.asset_name,
            source_corpus=kwargs.get('corpus_display_name', 'mock-corpus'),
            rag_context_snapshot=kwargs.get('rag_context', ''),
            narration_script=script_objects,
            ai_total_usage=usage
        )
        return result.model_dump()

    # --- 辅助方法 ---

    def _assemble_prompt_string(self, context: str, config: NarrationServiceConfig) -> str:
        """Prompt 组装 (Type Safe)"""
        lang = config.lang
        control = config.control_params
        custom = control.custom_prompts

        asset_name = config.asset_name
        if config.narrative_dataset and config.narrative_dataset.project_metadata:
            asset_name = config.narrative_dataset.project_metadata.asset_name

        render_ctx = {
            "character": control.perspective_character or "主角",
            "asset_name": asset_name,
            "minutes": control.target_duration_minutes or 3.0,
            "target_chars": int((control.target_duration_minutes or 3.0) * 60 * config.speaking_rate)
        }

        perspective = self._resolve_prompt_content(lang, "perspectives", control.perspective, custom).format(
            **render_ctx)
        style = self._resolve_prompt_content(lang, "styles", control.style, custom).format(**render_ctx)
        focus = self._resolve_prompt_content(lang, "focus", control.narrative_focus, custom).format(**render_ctx)

        constraints = ""
        lang_defs = self.prompt_definitions.get(lang, {})
        if control.target_duration_minutes:
            c_def = lang_defs.get("constraints", {})
            constraints = f"\n{c_def.get('duration_guideline', '')}{c_def.get('char_limit_instruction', '')}".format(
                **render_ctx)

        base_template = self._load_prompt_template(lang, "narration_generator")
        return base_template.format(
            perspective=perspective, style=style, narrative_focus=focus + constraints, rag_context=context
        )

    def _resolve_prompt_content(self, lang: str, category: str, key: str, custom_obj: Any = None) -> str:
        """
        解析 Prompt 内容 (Strict Mode / Strategy A)

        逻辑:
        1. 检查 Custom。
        2. 检查 Preset。
        3. [Strict] 如果都找不到，直接抛出异常，拒绝执行。
        """
        # 1. Custom 优先 (Safe Check)
        if custom_obj:
            if category == "styles" and getattr(custom_obj, 'style', None): return custom_obj.style
            if category == "focus" and getattr(custom_obj, 'narrative_focus', None): return custom_obj.narrative_focus

        # 2. 加载语言包
        lang_defs = self.prompt_definitions.get(lang, {})
        # [Strict Option] 如果连语言都不支持，是否要报错？
        # 考虑到 i18n 配置可能滞后，这里通常保留 fallback 到 'zh' 的容错，
        # 但既然你要求严格，如果完全找不到该语言定义，也可以视为一种配置错误。
        # 这里我保留了对“语言”的最低限度容错（防止服务崩溃），但对“业务 Key”进行强校验。
        if not lang_defs:
            self.logger.warning(f"Language '{lang}' not found, falling back to 'zh'.")
            lang_defs = self.prompt_definitions.get("zh", {})

        cat_defs = lang_defs.get(category, {})
        content = cat_defs.get(key, "")

        # 3. [Strategy A] 严格校验 (Strict Validation)
        # 如果既不是 Custom，又在 Preset 里找不到对应的 Prompt 内容，直接报错。
        if not content:
            error_msg = (
                f"Strict Protocol Violation: Invalid parameter for [{category}]. "
                f"Value '{key}' is not defined in system presets, and no custom prompt provided."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        return content