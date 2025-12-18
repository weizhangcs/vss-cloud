# 文件路径: ai_services/analysis/character/character_identifier.py
# 描述: [重构后] 角色客观事实识别服务 (V5.2 Schema-Driven)。
#       已适配 NarrativeDataset (V6 Strict Mode)，从字典操作转向对象操作。

import json
import logging
from pathlib import Path
from typing import Dict, Any, Union, List
from datetime import datetime
from collections import defaultdict
from pydantic import ValidationError

# 导入项目内部依赖
from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from core.error_codes import ErrorCode
from core.exceptions import BizException

# [新增依赖] 引入公共数据基座
from ai_services.biz_services.narrative_dataset import NarrativeDataset

from .schemas import CharacterAnalysisResponse


class CharacterIdentifier(AIServiceMixin):
    """
    角色事实识别器服务 (Character Identifier Service).

    本服务负责从结构化的 NarrativeDataset 中，为指定角色识别并提取客观事实。
    它的所有外部依赖通过构造函数注入，保持无状态和可移植性。
    """
    SERVICE_NAME = "character_identifier"
    HAS_OWN_DATADIR = True

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
        # 核心依赖：日志记录器和工作目录
        self.logger = logger
        self.work_dir = Path(base_path) if base_path else Path('.')

        # 核心组件
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator

        # 路径依赖
        self.prompts_dir = prompts_dir
        self.localization_path = localization_path
        self.schema_path = schema_path

        # 内部状态
        self.labels = {}
        self.logger.info("CharacterIdentifier Service initialized (Schema-Driven).")

    def execute(self, enhanced_script_path: Path, characters_to_analyze: List[str], **kwargs) -> Dict[str, Any]:
        """
        执行角色事实识别任务的主入口点。

        流程：加载Dataset -> 预处理索引 -> 循环处理角色 -> 聚合结果。
        """
        session_start_time = datetime.now()
        try:
            # --- 步骤 1: 环境准备 ---
            # 优先级: 1. API请求 'lang' -> 2. YAML 'default_lang' -> 3. 兜底 'en'
            lang = kwargs.get('lang', kwargs.get('default_lang', 'en'))

            self._load_localization_file(self.localization_path, lang)

            # [适配修改] 强校验加载 NarrativeDataset
            with enhanced_script_path.open('r', encoding='utf-8') as f:
                raw_data = json.load(f)

            # 这一步会触发 Strict Mode 校验，如果 JSON 结构不符合契约，直接抛出 ValidationError
            dataset = NarrativeDataset(**raw_data)

            # --- 步骤 2: 预处理 ---
            # [适配修改] 传入 dataset 对象而非 dict
            direct_scenes, mentioned_scenes = self._build_character_scene_index(dataset)

            all_facts_by_character = defaultdict(list)
            total_usage = {}
            scene_chunk_size = kwargs.get('default_scene_chunk_size', 10)

            # --- 步骤 3: 核心循环 ---
            for char_name in characters_to_analyze:
                # 获取相关场景ID
                all_relevant_ids = sorted(
                    list(direct_scenes.get(char_name, set()).union(mentioned_scenes.get(char_name, set()))))

                if not all_relevant_ids:
                    self.logger.info(f"角色 '{char_name}' 没有相关的场景，已跳过。")
                    continue

                # 分块处理 (避免 Token 溢出)
                scene_chunks = [all_relevant_ids[j:j + scene_chunk_size] for j in
                                range(0, len(all_relevant_ids), scene_chunk_size)]

                for chunk_index, chunk_of_ids in enumerate(scene_chunks):
                    chunk_direct_ids = {sid for sid in chunk_of_ids if sid in direct_scenes.get(char_name, set())}
                    chunk_mentioned_ids = {sid for sid in chunk_of_ids if sid in mentioned_scenes.get(char_name, set())}

                    # 移除 lang 参数避免重复传参
                    other_params = kwargs.copy()
                    other_params.pop('lang', None)

                    # [适配修改] 传入 dataset 对象
                    facts, usage = self._identify_facts_for_character(
                        char_name,
                        dataset,  # Pass Dataset Object
                        chunk_direct_ids,
                        chunk_mentioned_ids,
                        lang=lang,
                        chunk_index=chunk_index,
                        **other_params
                    )

                    if facts:
                        all_facts_by_character[char_name].extend(facts)
                    self._aggregate_usage(total_usage, usage)

            # --- 步骤 4: 任务收尾与报告生成 ---
            model_name = kwargs.get('model', 'gemini-2.5-flash')
            total_cost = self.cost_calculator.calculate(model_name, total_usage)
            session_duration = (datetime.now() - session_start_time).total_seconds()

            final_usage_report = {
                "model_name": model_name,
                "prompt_tokens": total_usage.get('prompt_tokens', 0),
                "completion_tokens": total_usage.get('completion_tokens', 0),
                "total_tokens": total_usage.get('total_tokens', 0),
                "session_duration_seconds": round(session_duration, 4),
                "request_count": total_usage.get('request_count', 0),
                **total_cost
            }
            # 清理 None 值
            final_usage_report = {k: v for k, v in final_usage_report.items() if v is not None}

            final_result = {
                "generation_date": datetime.now().isoformat(),
                "source_file": enhanced_script_path.name,
                "identified_facts_by_character": dict(all_facts_by_character),
            }

            return {"status": "success", "data": {"result": final_result, "usage": final_usage_report}}

        except Exception as e:
            self.logger.critical(f"执行人物事实识别时出错: {e}", exc_info=True)
            raise

    def _identify_facts_for_character(self, char_name: str, dataset: NarrativeDataset, direct_scene_ids: set,
                                      mentioned_scene_ids: set, lang: str, **kwargs) -> tuple[List[Dict], Dict]:
        """
        为单个角色的一批场景调用AI进行事实识别。
        [适配修改] script_data Dict -> dataset NarrativeDataset
        """
        chunk_index = kwargs.get('chunk_index', 0)

        # 步骤 1: 构建AI上下文
        dossier = self._build_for_character_identifier(
            char_name=char_name, dataset=dataset, direct_ids=direct_scene_ids,
            mentioned_ids=mentioned_scene_ids, labels=self.labels
        )
        if not dossier.strip():
            return [], {}

        # 步骤 2: 加载 Schema
        definitions_text, schema_data = self._load_and_format_fact_definitions(lang)

        # 步骤 3: 构建 Prompt
        prompt = self._build_prompt(
            prompt_name='character_identifier',
            character_name=char_name,
            rich_character_dossier=dossier,
            fact_attribute_definitions=definitions_text,
            lang=lang,
            **kwargs
        )

        if kwargs.get('debug', False):
            self.logger.debug("正在保存dossier和prompt用于调试...")
            self._save_debug_artifact("dossier.txt", dossier, char_name, chunk_index)
            self._save_debug_artifact("prompt.txt", prompt, char_name, chunk_index)

        # 步骤 4: 调用AI
        response_data, usage = self.gemini_processor.generate_content(
            model_name=kwargs.get('default_model', 'gemini-2.5-flash'),
            prompt=prompt,
            temperature=kwargs.get('default_temp', 0.1)
        )

        # 强校验：Pydantic Validation
        try:
            validated_response = CharacterAnalysisResponse(**response_data)
            validated_facts = validated_response.identified_facts
        except ValidationError as e:
            self.logger.error(f"LLM Schema Validation Failed: {e}")
            self.logger.debug(f"Raw Response: {response_data}")
            raise BizException(
                ErrorCode.LLM_INFERENCE_ERROR,
                msg=f"Gemini output structure invalid: {str(e)}",
                data={"raw_snippet": str(response_data)[:200]}
            )

        facts = [f.model_dump() for f in validated_facts]

        # 步骤 5: 后处理 (注入 type)
        if facts and schema_data:
            self.logger.info(f"为 {len(facts)} 个已识别事实注入属性类型 'type'...")
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

        return facts, usage

    def _load_and_format_fact_definitions(self, lang: str) -> tuple[str, dict]:
        """
        加载并格式化事实属性定义 (保持原逻辑，操作的是独立的 schema json).
        """
        try:
            with self.schema_path.open('r', encoding='utf-8') as f:
                schema_data_full = json.load(f)

            schema_data = schema_data_full.get(lang, {})
            attribute_format_labels = self.labels.get('attribute_labels', {})
            definitions_text_lines = []

            if not schema_data:
                self.logger.warning(f"在 fact_attributes.json 中未找到语言 '{lang}' 的 schema 数据。")
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
            self.logger.error(f"加载事实属性纲要时失败: {e}", exc_info=True)
            return "Error: Could not load attribute definitions.", {}

    def _build_character_scene_index(self, dataset: NarrativeDataset) -> tuple[Dict[str, set], Dict[str, set]]:
        """
        构建角色场景索引 (适配 Object Access).
        """
        direct_scenes = defaultdict(set)
        mentioned_scenes = defaultdict(set)

        # [适配修改] 从 dataset.scenes.values() 获取列表
        scenes = list(dataset.scenes.values())

        # [适配修改] 属性访问
        all_characters = {
            dialogue.speaker
            for scene in scenes
            for dialogue in scene.dialogues
            if dialogue.speaker
        }

        for scene in scenes:
            scene_id = scene.local_id  # [适配修改] local_id (int)
            dialogues_in_scene = scene.dialogues

            # 确定当前场景有哪些角色发言（直接出场）
            speakers_in_scene = {d.speaker for d in dialogues_in_scene if d.speaker}
            for speaker in speakers_in_scene:
                direct_scenes[speaker].add(scene_id)

            # 确定哪些【未出场】角色在对话中被提及（间接提及）
            all_dialogue_text = " ".join([d.content for d in dialogues_in_scene])
            chars_to_check_mention = all_characters - speakers_in_scene
            for char_name in chars_to_check_mention:
                if char_name and char_name in all_dialogue_text:
                    mentioned_scenes[char_name].add(scene_id)

        return dict(direct_scenes), dict(mentioned_scenes)

    def _save_debug_artifact(self, filename: str, content: str, character_name: str, chunk_index: int):
        """保存调试文件"""
        try:
            debug_dir = self.work_dir / "_debug_artifacts"
            debug_dir.mkdir(exist_ok=True)
            unique_filename = f"{character_name}_chunk_{chunk_index}_{filename}"
            (debug_dir / unique_filename).write_text(content, encoding='utf-8')
            self.logger.info(f"调试文件已保存: {unique_filename}")
        except Exception as e:
            self.logger.warning(f"保存调试文件 {filename} 时失败: {e}")

    def _build_for_character_identifier(
            self,
            char_name: str,
            dataset: NarrativeDataset,
            direct_ids: set,
            mentioned_ids: set,
            labels: Dict
    ) -> str:
        """
        构建 dossier (适配 Object Access).
        """
        dossier_labels = labels.get('dossier', {})

        log_entries = []
        all_relevant_ids = sorted(list(direct_ids.union(mentioned_ids)))

        for scene_id in all_relevant_ids:
            # [适配修改] Dataset keys are strings
            scene = dataset.scenes.get(str(scene_id))
            if not scene: continue

            scene_type_text = dossier_labels.get('dossier_direct_header',
                                                 '') if scene_id in direct_ids else dossier_labels.get(
                'dossier_mentioned_header', '')
            log_entries.append(dossier_labels.get('dossier_scene_header', "--- Scene ID: {scene_id} ---").format(
                scene_id=scene_id) + f" ({scene_type_text})")

            # [适配修改] 属性访问 character_dynamics
            log_entries.append(
                f"{dossier_labels.get('dossier_dynamics_label', 'Plot Dynamics:')} {scene.character_dynamics}")

            # [适配修改] 属性访问 captions (List[CaptionItem])
            if scene.captions:
                log_entries.append(dossier_labels.get('dossier_caption_header', 'Relevant Captions:'))
                for cap in scene.captions:
                    log_entries.append(f"  - {cap.content}")

            # [适配修改] 属性访问 dialogues (List[DialogueItem])
            if scene.dialogues:
                log_entries.append(dossier_labels.get('dossier_dialogue_header', 'Relevant Dialogue:'))
                for diag in scene.dialogues:
                    # 使用 model_dump() 转为 dict 以供字符串格式化使用
                    line = dossier_labels.get('dossier_dialogue_line', "  - {speaker}: {content}")
                    log_entries.append(line.format(**diag.model_dump()))

        return "\n".join(log_entries) if log_entries else dossier_labels.get('no_info', 'No relevant scenes.')