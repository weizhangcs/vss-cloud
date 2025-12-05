# 文件路径: ai_services/analysis/character/character_identifier.py
# 描述: [重构后] 角色客观事实识别服务，已完全解耦，通过依赖注入模式运行。
# 版本: 5.1 (Final Reviewed)

import json
import logging
from pathlib import Path
from typing import Dict, Any, Union, List
from datetime import datetime
from collections import defaultdict
from pydantic import ValidationError

# 导入项目内部依赖
from ai_services.common.gemini.ai_service_mixin import AIServiceMixin
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator import CostCalculator
from core.error_codes import ErrorCode
from core.exceptions import BizException

from .schemas import CharacterAnalysisResponse

class CharacterIdentifier(AIServiceMixin):
    """
    角色事实识别器服务 (Character Identifier Service)。

    本服务负责从结构化的剧本数据中，为指定角色识别并提取客观事实。
    它的所有外部依赖（如AI处理器、计费器、路径配置等）都通过构造函数注入，
    使其成为一个与框架无关的、可移植的业务逻辑单元。
    """
    # SERVICE_NAME 用于在系统中唯一标识此服务，例如用于定位语言包或prompt文件。
    SERVICE_NAME = "character_identifier"
    # HAS_OWN_DATADIR 是一个元数据标志，用于指示服务是否需要一个独立的工作目录。
    # (在此解耦版本中，此标志的实际作用已由外部调用方处理)
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
        初始化CharacterIdentifier服务。

        这是一个“依赖注入”构造函数，它不创建任何复杂的对象，只接收和存储外部传入的依赖。

        Args:
            gemini_processor (GeminiProcessor): 用于与AI模型通信的处理器实例。
            cost_calculator (CostCalculator): 用于计算AI调用成本的实例。
            prompts_dir (Path): 包含此服务所需prompt模板的目录路径。
            localization_path (Path): 指向特定语言包JSON文件的完整路径。
            schema_path (Path): 指向事实属性定义（fact_attributes.json）的完整路径。
            logger (logging.Logger): 一个已配置好的日志记录器实例。
            base_path (Union[str, Path, None]): 服务的工作目录，用于存储调试文件等。
        """
        # 核心依赖：日志记录器和工作目录
        self.logger = logger
        self.work_dir = Path(base_path) if base_path else Path('.')

        # 核心组件：接收已实例化的服务依赖
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator

        # 路径依赖：接收已解析好的、具体的资源文件路径
        self.prompts_dir = prompts_dir          # Prompt模板目录
        self.localization_path = localization_path  # 语言包文件路径
        self.schema_path = schema_path          # 事实属性纲要文件路径

        # 内部状态：用于存储加载后的语言包内容
        self.labels = {}
        self.logger.info("CharacterIdentifier Service initialized (decoupled).")

    def execute(self, enhanced_script_path: Path, characters_to_analyze: List[str], **kwargs) -> Dict[str, Any]:
        """
        执行角色事实识别任务的主入口点。

        此方法编排了整个识别流程：加载数据 -> 预处理 -> 循环处理每个角色 -> 聚合结果 -> 返回报告。
        它是一个纯业务逻辑方法，不处理任何外部框架的特定逻辑（如HTTP请求或文件保存）。

        Args:
            enhanced_script_path (Path): 指向输入剧本JSON文件的Path对象。
            characters_to_analyze (List[str]): 需要进行事实识别的角色名称列表。
            **kwargs: 其他可选参数，会被透传给下层方法。常用参数包括:
                - lang (str): 使用的语言，如 'zh' 或 'en'。
                - model (str): 要使用的AI模型名称。
                - temp (float): AI模型的温度参数。
                - scene_chunk_size (int): 单次AI调用处理的场景数量上限。
                - debug (bool): 是否开启调试模式（如保存中间文件）。

        Returns:
            Dict[str, Any]: 一个包含任务状态、最终结果和AI用量报告的字典。
        """
        session_start_time = datetime.now()
        try:
            # --- 步骤 1: 环境准备 ---
            # [修改点 1] 实现中心化配置优先级逻辑
            # 优先级: 1. API请求中的 'lang' -> 2. YAML中的 'default_lang' -> 3. 代码兜底 'en'
            # 注意：kwargs 是 merged 后的结果，包含了 service_params 和 ai_config
            lang = kwargs.get('lang', kwargs.get('default_lang', 'en'))

            self._load_localization_file(self.localization_path, lang)

            with enhanced_script_path.open('r', encoding='utf-8') as f:
                script_data = json.load(f)

            # --- 步骤 2: 预处理 ---
            direct_scenes, mentioned_scenes = self._build_character_scene_index(script_data)
            all_facts_by_character = defaultdict(list)
            total_usage = {}
            # 从 kwargs 中获取 scene_chunk_size，不再硬编码 10
            scene_chunk_size = kwargs.get('default_scene_chunk_size', 10)

            # --- 步骤 3: 核心循环 ---
            # 遍历每一个需要分析的角色。
            for char_name in characters_to_analyze:
                # 获取与该角色相关的所有场景ID，并去重、排序。
                all_relevant_ids = sorted(
                    list(direct_scenes.get(char_name, set()).union(mentioned_scenes.get(char_name, set()))))

                if not all_relevant_ids:
                    self.logger.info(f"角色 '{char_name}' 没有相关的场景，已跳过。")
                    continue

                # 为了防止单次API调用上下文过长，将相关场景ID列表分块处理。
                scene_chunks = [all_relevant_ids[j:j + scene_chunk_size] for j in
                                range(0, len(all_relevant_ids), scene_chunk_size)]

                # 遍历每个场景块，分别调用AI进行处理。
                for chunk_index, chunk_of_ids in enumerate(scene_chunks):
                    # 从当前块中，再次区分哪些是直接出现，哪些是间接提及。
                    chunk_direct_ids = {sid for sid in chunk_of_ids if sid in direct_scenes.get(char_name, set())}
                    chunk_mentioned_ids = {sid for sid in chunk_of_ids if sid in mentioned_scenes.get(char_name, set())}

                    # 为了避免向 `_identify_facts_for_character` 方法重复传递 `lang` 参数，
                    # 我们先从 kwargs 的副本中移除它，然后再解包。
                    other_params = kwargs.copy()
                    other_params.pop('lang', None)

                    # 调用核心的AI识别方法。
                    facts, usage = self._identify_facts_for_character(
                        char_name, script_data, chunk_direct_ids, chunk_mentioned_ids,
                        lang=lang,
                        chunk_index=chunk_index,
                        **other_params
                    )

                    # 将单次调用的结果和用量聚合到总结果中。
                    if facts:
                        all_facts_by_character[char_name].extend(facts)
                    self._aggregate_usage(total_usage, usage)

            # --- 步骤 4: 任务收尾与报告生成 ---
            # 使用注入的 cost_calculator 计算总成本。
            model_name = kwargs.get('model', 'gemini-2.5-flash')
            total_cost = self.cost_calculator.calculate(model_name, total_usage)
            session_duration = (datetime.now() - session_start_time).total_seconds()

            # 构建详细的AI用量报告。
            final_usage_report = {
                "model_name": model_name,
                "prompt_tokens": total_usage.get('prompt_tokens', 0),
                "completion_tokens": total_usage.get('completion_tokens', 0),
                "total_tokens": total_usage.get('total_tokens', 0),
                "session_duration_seconds": round(session_duration, 4),
                "request_count": total_usage.get('request_count', 0),
                **total_cost
            }
            final_usage_report = {k: v for k, v in final_usage_report.items() if v is not None}

            # 构建最终的完整结果，其中不包含重复的用量报告。
            final_result = {
                "generation_date": datetime.now().isoformat(),
                "source_file": enhanced_script_path.name,
                "identified_facts_by_character": dict(all_facts_by_character),
            }

            # 返回一个标准的、包含状态和数据的信封(envelope)结构。
            return {"status": "success", "data": {"result": final_result, "usage": final_usage_report}}

        except Exception as e:
            # 捕获所有异常，记录严重错误日志，然后重新抛出，让上层调用者（如Celery Task）处理。
            self.logger.critical(f"执行人物事实识别时出错: {e}", exc_info=True)
            raise

    def _identify_facts_for_character(self, char_name: str, script_data: Dict[str, Any], direct_scene_ids: set,
                                      mentioned_scene_ids: set, lang: str, **kwargs) -> tuple[List[Dict], Dict]:
        """
        为单个角色的一批场景调用AI进行事实识别。

        这是事实识别的“工作马”，负责：
        1. 构建供AI阅读的富文本文档（dossier）。
        2. 加载并格式化事实属性定义，注入到Prompt中。
        3. 构建完整的Prompt。
        4. (可选) 保存调试用的中间文件。
        5. 调用AI模型并获取响应。
        6. 对AI返回的结果进行后处理，注入元数据。

        Args:
            char_name (str): 目标角色名称。
            script_data (Dict): 完整的剧本数据字典。
            direct_scene_ids (set): 此批次中角色直接出场的场景ID集合。
            mentioned_scene_ids (set): 此批次中角色被提及的场景ID集合。
            lang (str): 要使用的语言 ('zh' or 'en')。
            **kwargs: 包含其他可选参数，如 chunk_index, debug, model, temp。

        Returns:
            tuple[List[Dict], Dict]: 一个元组，包含识别出的事实列表和本次AI调用的用量信息。
        """

        """
                为单个角色的一批场景调用AI进行事实识别。
                """
        chunk_index = kwargs.get('chunk_index', 0)

        # 步骤 1: 构建AI上下文（角色专属情报报告）
        dossier = self._build_for_character_identifier(
            char_name=char_name, script_data=script_data, direct_ids=direct_scene_ids,
            mentioned_ids=mentioned_scene_ids, labels=self.labels
        )
        if not dossier.strip():
            # 如果没有内容，提前返回，避免不必要的AI调用
            return [], {}

        # 步骤 2: 加载并格式化事实属性定义
        definitions_text, schema_data = self._load_and_format_fact_definitions(lang)

        # 步骤 3: 构建最终的Prompt
        prompt = self._build_prompt(
            prompt_name='character_identifier',
            character_name=char_name,
            rich_character_dossier=dossier,
            fact_attribute_definitions=definitions_text,
            lang=lang,  # 显式传递lang给_build_prompt
            **kwargs
        )

        # 在调试模式下，保存构建好的上下文和Prompt，便于问题排查
        if kwargs.get('debug', False):
            self.logger.debug("正在保存dossier和prompt用于调试...")
            self._save_debug_artifact("dossier.txt", dossier, char_name, chunk_index)
            self._save_debug_artifact("prompt.txt", prompt, char_name, chunk_index)

        # 步骤 4: 调用AI模型
        # 步骤 4: 调用AI模型
        # 从 kwargs 中获取 model 和 temp，不再硬编码
        response_data, usage = self.gemini_processor.generate_content(
            model_name=kwargs.get('default_model', 'gemini-2.5-flash'),
            prompt=prompt,
            temperature=kwargs.get('default_temp', 0.1)
        )

        # [核心修改] 强校验：LLM 返回的数据是否符合契约？
        try:
            validated_response = CharacterAnalysisResponse(**response_data)
            # 获取校验后的纯净数据 (Pydantic Model List)
            validated_facts = validated_response.identified_facts
        except ValidationError as e:
            # [关键] 捕获 Schema 错误，标记为 LLM 推理失败
            self.logger.error(f"LLM Schema Validation Failed: {e}")
            self.logger.debug(f"Raw Response: {response_data}")

            # 抛出业务异常，Task 层会捕获并标记为 Failed
            # 可以在 data 中附带 raw response 方便调试
            raise BizException(
                ErrorCode.LLM_INFERENCE_ERROR,
                msg=f"Gemini output structure invalid: {str(e)}",
                data={"raw_snippet": str(response_data)[:200]}
            )

        # [修改] 后续逻辑使用 validated_facts
        # 注意：validated_facts 是 Model 对象列表，需要转回 dict 给下游处理
        facts = [f.dict() for f in validated_facts]

        # 步骤 5: 后处理 - 为AI返回的事实注入'type'元数据
        # AI只返回属性名称，我们根据schema为其补充属性类别（如'恒定', '状态性'等）
        if facts and schema_data:
            self.logger.info(f"为 {len(facts)} 个已识别事实注入属性类型 'type'...")

            # 创建一个从显示名称到内部键名的映射，以应对多语言环境
            display_name_to_key_map = {
                v.get('display_name', k): k for k, v in schema_data.items()
            }

            # 从语言包获取默认类型，以防AI返回一个schema中不存在的属性
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
        从注入的 schema_path 加载并格式化事实属性定义。

        此方法读取 fact_attributes.json 文件，并将其内容转换为对AI友好的、
        结构化的纯文本描述，用于注入到Prompt中。

        Returns:
            tuple[str, dict]: 一个元组，第一个元素是格式化后的文本，第二个是原始的schema字典。
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

    def _build_character_scene_index(self, script_data: Dict[str, Any]) -> tuple[Dict[str, set], Dict[str, set]]:
        """
        构建角色场景索引，区分直接出场和间接被提及的场景。

        这是一个预处理步骤，它遍历一次所有场景，生成一个高效的查找表，
        避免在主循环中反复进行昂贵的文本搜索。

        Args:
            script_data (Dict[str, Any]): 包含所有场景数据的剧本字典。

        Returns:
            tuple[Dict[str, set], Dict[str, set]]: 两个字典，
                第一个是 {角色名: {直接出场场景ID集合}}，
                第二个是 {角色名: {被提及场景ID集合}}。
        """
        direct_scenes = defaultdict(set)
        mentioned_scenes = defaultdict(set)
        scenes = list(script_data.get('scenes', {}).values())

        # 优化：预先提取一次剧中所有出现过的角色名。
        all_characters = {
            dialogue.get('speaker')
            for scene in scenes
            for dialogue in scene.get('dialogues', [])
            if dialogue.get('speaker')
        }

        for scene in scenes:
            scene_id = scene['id']
            dialogues_in_scene = scene.get('dialogues', [])

            # 确定当前场景有哪些角色发言（直接出场）。
            speakers_in_scene = {d.get('speaker') for d in dialogues_in_scene if d.get('speaker')}
            for speaker in speakers_in_scene:
                direct_scenes[speaker].add(scene_id)

            # 确定哪些【未出场】角色在对话中被提及（间接提及）。
            all_dialogue_text = " ".join([d.get('content', '') for d in dialogues_in_scene])
            chars_to_check_mention = all_characters - speakers_in_scene
            for char_name in chars_to_check_mention:
                if char_name and char_name in all_dialogue_text:
                    mentioned_scenes[char_name].add(scene_id)

        return dict(direct_scenes), dict(mentioned_scenes)

    def _save_debug_artifact(self, filename: str, content: str, character_name: str, chunk_index: int):
        """
        将调试用的中间产物（如dossier, prompt）保存到文件。

        文件名将包含角色和批次信息，以防止在单次运行中被覆盖。

        Args:
            filename (str): 基础文件名 (e.g., "dossier.txt").
            content (str): 要写入文件的内容。
            character_name (str): 当前处理的角色名。
            chunk_index (int): 当前处理的场景批次索引。
        """
        try:
            debug_dir = self.work_dir / "_debug_artifacts"
            # 确保目录存在，如果已存在则不做任何事。
            debug_dir.mkdir(exist_ok=True)
            unique_filename = f"{character_name}_chunk_{chunk_index}_{filename}"
            (debug_dir / unique_filename).write_text(content, encoding='utf-8')
            self.logger.info(f"调试文件已保存: {unique_filename}")
        except Exception as e:
            self.logger.warning(f"保存调试文件 {filename} 时失败: {e}")

    def _build_for_character_identifier(
            self,
            char_name: str,
            script_data: Dict,
            direct_ids: set,
            mentioned_ids: set,
            labels: Dict
    ) -> str:
        """
        为 CharacterIdentifier 服务，构建用于事实识别的富文本角色档案（dossier）。

        此方法从原始剧本数据中提取与指定角色相关的场景信息，并将其格式化成
        一个对AI模型友好的、连贯的纯文本文档。

        Args:
            char_name (str): 目标角色名称。
            script_data (Dict): 完整的剧本数据字典。
            direct_ids (set): 角色直接出场的场景ID集合。
            mentioned_ids (set): 角色被间接提及的场景ID集合。
            labels (Dict): 从语言包加载的标签字典，用于本地化输出。

        Returns:
            str: 格式化后的富文本字符串。
        """
        dossier_labels = labels.get('dossier', {})
        scenes_map = {int(k): v for k, v in script_data.get('scenes', {}).items()}
        log_entries = []
        all_relevant_ids = sorted(list(direct_ids.union(mentioned_ids)))

        for scene_id in all_relevant_ids:
            scene = scenes_map.get(scene_id)
            if not scene: continue

            # 根据场景类型（直接/间接）添加不同的标题，为AI提供更多上下文
            scene_type_text = dossier_labels.get('dossier_direct_header',
                                                 '') if scene_id in direct_ids else dossier_labels.get(
                'dossier_mentioned_header', '')
            log_entries.append(dossier_labels.get('dossier_scene_header', "--- Scene ID: {scene_id} ---").format(
                scene_id=scene_id) + f" ({scene_type_text})")

            # 添加情节动态
            log_entries.append(
                f"{dossier_labels.get('dossier_dynamics_label', 'Plot Dynamics:')} {scene.get('character_dynamics', '')}")

            # 添加相关的提词（Captions）
            captions = scene.get('captions', [])
            if captions:
                log_entries.append(dossier_labels.get('dossier_caption_header', 'Relevant Captions:'))
                for event in captions:
                    log_entries.append(f"  - {event.get('content', '')}")

            # 添加相关的对话
            dialogues = scene.get('dialogues', [])
            if dialogues:
                log_entries.append(dossier_labels.get('dossier_dialogue_header', 'Relevant Dialogue:'))
                for event in dialogues:
                    # 使用语言包中的模板来格式化对话行
                    log_entries.append(
                        dossier_labels.get('dossier_dialogue_line', "  - {speaker}: {content}").format(**event))

        return "\n".join(log_entries) if log_entries else dossier_labels.get('no_info', 'No relevant scenes.')