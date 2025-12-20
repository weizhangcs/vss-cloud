# 文件路径: ai_services/biz_services/analysis/character/character_identifier.py
# 描述: [重构后] 角色客观事实识别服务 (V6 Schema-First / Type-Safe)。
#       适配新的 GeminiProcessor(V2) 和 AIServiceMixin(V5)。

import json
import logging
from pathlib import Path
from typing import Dict, Any, Union, List
from datetime import datetime
from collections import defaultdict

# 导入项目内部依赖
from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from core.error_codes import ErrorCode
from core.exceptions import BizException

# [新增] 引入 Schema 用于 API 强约束
from .schemas import CharacterAnalysisResponse

# [新增] 引入公共数据基座
from ai_services.biz_services.narrative_dataset import NarrativeDataset


class CharacterIdentifier(AIServiceMixin):
    """
    角色事实识别器服务 (Character Identifier Service).

    Refactor Note:
    - 移除了所有正则表达式和 JSON 修复逻辑。
    - 使用 response_schema 直接获取 Pydantic 对象。
    - 显式传递 prompts_dir 以符合 Mixin V5 契约。
    """
    SERVICE_NAME = "character_identifier"

    # [Standardized Config]
    DEFAULT_SCENE_CHUNK_SIZE = 10
    DEFAULT_TEMPERATURE = 0.1

    def __init__(self,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator,
                 prompts_dir: Path,
                 localization_path: Path,
                 schema_path: Path,
                 logger: logging.Logger,
                 base_path: Union[str, Path, None] = None):
        """
        初始化 CharacterIdentifier 服务 (依赖注入)。
        """
        self.logger = logger
        self.work_dir = Path(base_path) if base_path else Path('.')

        # 核心组件
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator

        # 路径依赖
        self.prompts_dir = prompts_dir
        self.localization_path = localization_path
        self.schema_path = schema_path

        self.labels = {}
        self.logger.info("CharacterIdentifier Service initialized (V6 Type-Safe).")

    def execute(self, enhanced_script_path: Path, characters_to_analyze: List[str], **kwargs) -> Dict[str, Any]:
        """
        执行角色事实识别任务的主入口点。
        """
        session_start_time = datetime.now()
        try:
            # --- 步骤 1: 环境准备 ---
            # [Standardized Config] 统一命名规范
            lang = kwargs.get('lang', 'en')  # 这里 lang 是核心业务参数，非 config，保持原样
            model_name = kwargs.get('model', 'gemini-2.5-flash')

            # 配置提取
            scene_chunk_size = kwargs.get('scene_chunk_size', self.DEFAULT_SCENE_CHUNK_SIZE)
            temperature = kwargs.get('temperature', self.DEFAULT_TEMPERATURE)

            self._load_localization_file(self.localization_path, lang)

            # 加载 NarrativeDataset
            with enhanced_script_path.open(encoding='utf-8') as f:
                raw_data = json.load(f)
            dataset = NarrativeDataset(**raw_data)

            # --- 步骤 2: 预处理 ---
            direct_scenes, mentioned_scenes = self._build_character_scene_index(dataset)

            all_facts_by_character = defaultdict(list)

            # 使用字典累加 Token 计数
            total_usage_accumulator = {}

            # --- 步骤 3: 核心循环 ---
            for char_name in characters_to_analyze:
                all_relevant_ids = sorted(
                    list(direct_scenes.get(char_name, set()).union(mentioned_scenes.get(char_name, set()))))

                if not all_relevant_ids:
                    self.logger.info(f"角色 '{char_name}' 没有相关的场景，已跳过。")
                    continue

                # 分块处理
                scene_chunks = [all_relevant_ids[j:j + scene_chunk_size] for j in
                                range(0, len(all_relevant_ids), scene_chunk_size)]

                for chunk_index, chunk_of_ids in enumerate(scene_chunks):
                    chunk_direct_ids = {sid for sid in chunk_of_ids if sid in direct_scenes.get(char_name, set())}
                    chunk_mentioned_ids = {sid for sid in chunk_of_ids if sid in mentioned_scenes.get(char_name, set())}

                    is_debug = kwargs.get('debug', self.gemini_processor.debug_mode)

                    other_params = kwargs.copy()
                    other_params['debug'] = is_debug
                    other_params['temperature'] = temperature
                    other_params.pop('lang', None)

                    # [Call] 核心处理
                    facts, usage_stats = self._identify_facts_for_character(
                        char_name,
                        dataset,
                        chunk_direct_ids,
                        chunk_mentioned_ids,
                        lang=lang,
                        model_name=model_name,
                        chunk_index=chunk_index,
                        **other_params
                    )

                    if facts:
                        all_facts_by_character[char_name].extend(facts)

                    # [Mixin] 聚合 UsageStats 对象到字典中
                    self._aggregate_usage(total_usage_accumulator, usage_stats)

            # --- 步骤 4: 任务收尾与报告生成 ---

            # [Cost] 构造最终的 UsageStats 对象用于计费
            # 注意：request_count 和 duration 已经在 _aggregate_usage 中累加
            final_stats = UsageStats(
                model_used=model_name,
                **total_usage_accumulator
            )

            # 计算金额
            cost_report = self.cost_calculator.calculate(final_stats)

            session_duration = (datetime.now() - session_start_time).total_seconds()

            # 构造包含金额的最终报告
            # [Fix] 使用 to_dict() 而非 model_dump() 以便扁平化输出 (如 total_tokens)
            final_usage_report = cost_report.to_dict()
            final_usage_report.update({
                "session_duration_seconds": round(session_duration, 4),
            })

            final_result = {
                "generation_date": datetime.now().isoformat(),
                "source_file": enhanced_script_path.name,
                "identified_facts_by_character": dict(all_facts_by_character),
            }

            return {"status": "success", "data": {"result": final_result, "usage": final_usage_report}}

        except Exception as e:
            self.logger.critical(f"执行人物事实识别时出错: {e}", exc_info=True)
            raise

    def _identify_facts_for_character(self,
                                      char_name: str,
                                      dataset: NarrativeDataset,
                                      direct_scene_ids: set,
                                      mentioned_scene_ids: set,
                                      lang: str,
                                      model_name: str,
                                      **kwargs) -> tuple[List[Dict], UsageStats]:
        """
        [Core Logic] 单次推理：Dossier -> Prompt -> Schema-Based Inference
        """
        chunk_index = kwargs.get('chunk_index', 0)
        # 获取透传下来的 temperature
        temperature = kwargs.get('temperature', self.DEFAULT_TEMPERATURE)

        # 1. 构建 Dossier
        dossier = self._build_for_character_identifier(
            dataset=dataset,
            direct_ids=direct_scene_ids,
            mentioned_ids=mentioned_scene_ids,
            labels=self.labels
        )
        if not dossier.strip():
            # [Fix] 显式填充 UsageStats 避免 Pydantic 校验错误
            empty_stats = UsageStats(
                model_used=model_name,
                prompt_tokens=0,
                cached_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                request_count=0,
                duration_seconds=0.0,
                timestamp=datetime.now().isoformat()
            )
            return [], empty_stats

        # 2. 加载定义
        definitions_text, schema_data = self._load_and_format_fact_definitions(lang)

        # 3. 构建 Prompt (V5 Explicit: 显式传递 prompts_dir 和 lang)
        prompt = self._build_prompt(
            prompts_dir=self.prompts_dir,  # [Explicit]
            prompt_name='character_identifier',
            lang=lang,  # [Explicit]
            # Context Variables
            character_name=char_name,
            rich_character_dossier=dossier,
            fact_attribute_definitions=definitions_text,
            **kwargs
        )

        # TODO: 如果是研发周期本地测试，没有问题，不需要修改。 如果是生产环境切换运维，那么需要携带task id
        if kwargs.get('debug', False):
            self._save_debug_artifact("prompt.txt", prompt, char_name, chunk_index)

        # 4. [Schema Engineering] 调用 AI
        # 直接传入 response_schema 类，无需 try-except 解析 JSON
        try:
            response_obj, usage_stats = self.gemini_processor.generate_content(
                model_name=model_name,
                prompt=prompt,
                response_schema=CharacterAnalysisResponse,  # [Key Change] 强类型契约
                temperature=temperature
            )
        except Exception as e:
            # 这里的异常已经是处理过的 RateLimitException 或 RuntimeError
            self.logger.error(f"AI Inference failed: {e}")
            raise BizException(ErrorCode.LLM_INFERENCE_ERROR, msg=f"AI Error: {e}")

        # 5. 后处理 (注入 type)
        # response_obj 已经是 CharacterAnalysisResponse 实例
        validated_facts = response_obj.identified_facts

        # 转为 Dict 列表以便注入 extra 字段 (type)
        facts = [f.model_dump() for f in validated_facts]

        if facts and schema_data:
            display_name_to_key_map = {
                v.get('display_name', k): k for k, v in schema_data.items()
            }
            default_type = self.labels.get('dossier', {}).get('default_fact_type',
                                                              'ephemeral' if lang == 'en' else '情境性')

            for fact in facts:
                ai_attribute_value = fact.get("attribute")
                internal_key = display_name_to_key_map.get(ai_attribute_value)

                if internal_key:
                    fact["type"] = schema_data[internal_key].get("type", default_type)
                else:
                    fact["type"] = default_type

        return facts, usage_stats

    def _load_and_format_fact_definitions(self, lang: str) -> tuple[str, dict]:
        """
        加载并格式化事实属性定义.
        """
        try:
            with self.schema_path.open(encoding='utf-8') as f:
                schema_data_full = json.load(f)

            schema_data = schema_data_full.get(lang, {})
            attribute_format_labels = self.labels.get('attribute_labels', {})
            definitions_text_lines = []

            if not schema_data:
                return "No attribute definitions found.", {}

            for key, data in schema_data.items():
                display_name = data.get('display_name', key)
                definitions_text_lines.append(f"- {display_name}:")
                desc_label = attribute_format_labels.get('description', 'Description')
                definitions_text_lines.append(f"  - {desc_label}: {data.get('description', 'N/A')}")
                if 'keywords' in data and data['keywords']:
                    keywords_label = attribute_format_labels.get('keywords', 'Keywords')
                    keywords_str = ", ".join([f'"{k}"' for k in data['keywords']])
                    definitions_text_lines.append(f"  - {keywords_label}: [{keywords_str}]")
                if 'type' in data:
                    type_label = attribute_format_labels.get('type', 'Type')
                    definitions_text_lines.append(f"  - {type_label}: {data.get('type')}")

            definitions_text = "\n".join(definitions_text_lines)
            return definitions_text, schema_data
        except Exception as e:
            self.logger.error(f"Error loading fact definitions: {e}")
            return "Error.", {}

    def _build_character_scene_index(self, dataset: NarrativeDataset) -> tuple[Dict[str, set], Dict[str, set]]:
        """
        构建角色场景索引 (适配 Object Access).
        """
        direct_scenes = defaultdict(set)
        mentioned_scenes = defaultdict(set)

        scenes = list(dataset.scenes.values())

        all_characters = {
            dialogue.speaker
            for scene in scenes
            for dialogue in scene.dialogues
            if dialogue.speaker
        }

        for scene in scenes:
            scene_id = scene.local_id
            dialogues_in_scene = scene.dialogues

            speakers_in_scene = {d.speaker for d in dialogues_in_scene if d.speaker}
            for speaker in speakers_in_scene:
                direct_scenes[speaker].add(scene_id)

            all_dialogue_text = " ".join([d.content for d in dialogues_in_scene])
            chars_to_check_mention = all_characters - speakers_in_scene
            for char_name in chars_to_check_mention:
                if char_name and char_name in all_dialogue_text:
                    mentioned_scenes[char_name].add(scene_id)

        return dict(direct_scenes), dict(mentioned_scenes)

    def _save_debug_artifact(self, filename: str, content: str, character_name: str, chunk_index: int):
        try:
            debug_dir = self.work_dir / "_debug_artifacts"
            debug_dir.mkdir(parents=True, exist_ok=True) # 建议加上 parents=True
            unique_filename = f"{character_name}_chunk_{chunk_index}_{filename}"
            (debug_dir / unique_filename).write_text(content, encoding='utf-8')
        except OSError as e:  # [Fix] 收窄异常范围并记录日志
            self.logger.warning(f"Failed to save debug artifact : {e}")

    def _build_for_character_identifier(
            self,
            dataset: NarrativeDataset,
            direct_ids: set,
            mentioned_ids: set,
            labels: Dict
    ) -> str:
        dossier_labels = labels.get('dossier', {})
        log_entries = []
        all_relevant_ids = sorted(list(direct_ids.union(mentioned_ids)))

        for scene_id in all_relevant_ids:
            scene = dataset.scenes.get(str(scene_id))
            if not scene: continue

            scene_type_text = dossier_labels.get('dossier_direct_header',
                                                 '') if scene_id in direct_ids else dossier_labels.get(
                'dossier_mentioned_header', '')
            log_entries.append(dossier_labels.get('dossier_scene_header', "--- Scene ID: {scene_id} ---").format(
                scene_id=scene_id) + f" ({scene_type_text})")

            log_entries.append(
                f"{dossier_labels.get('dossier_dynamics_label', 'Plot Dynamics:')} {scene.character_dynamics}")

            if scene.captions:
                log_entries.append(dossier_labels.get('dossier_caption_header', 'Relevant Captions:'))
                for cap in scene.captions:
                    log_entries.append(f"  - {cap.content}")

            if scene.dialogues:
                log_entries.append(dossier_labels.get('dossier_dialogue_header', 'Relevant Dialogue:'))
                for diag in scene.dialogues:
                    # 使用 model_dump()
                    line = dossier_labels.get('dossier_dialogue_line', "  - {speaker}: {content}")
                    log_entries.append(line.format(**diag.model_dump()))

        return "\n".join(log_entries) if log_entries else dossier_labels.get('no_info', 'No relevant scenes.')