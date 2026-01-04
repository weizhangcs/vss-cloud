# ai_services/biz_services/character_pre_annotator/service.py

import json
import math
import logging
from pathlib import Path
from typing import Dict, Any, List

from django.conf import settings
from google.cloud import storage

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    CharacterPreAnnotatorPayload,
    SubtitleInputItem, OptimizedSubtitleItem,
    BatchRoleInferenceResponse, SpeakerNormalizationResponse,
)

logger = logging.getLogger(__name__)


class CharacterPreAnnotatorService(AIServiceMixin):
    """
    [Service] 角色预标注服务 (V4.0 JSON-Native Edition)

    重构要点：
    1. 移除 SRT 解析与 ASS 生成，实现全 JSON 载荷处理。
    2. 采用增量回传模式，输出仅包含 index, speaker, reasoning。
    3. 支持本地与 GCS 混合路径读取。
    """
    SERVICE_NAME = "character_pre_annotator"

    def __init__(self,
                 logger: logging.Logger,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Character Pre-Annotation (JSON-Native Mode)...")

        # 1. 载荷校验
        try:
            task_input = CharacterPreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        # 2. 数据加载：直接加载 SubtitleInputItem 列表
        all_lines = self._load_subtitle_items(task_input.subtitle_path)
        total_lines = len(all_lines)
        self.logger.info(f"Loaded {total_lines} items. Batch Size: {task_input.batch_size}")

        # 3. 准备推理上下文
        chars_str = ", ".join(task_input.known_characters) if task_input.known_characters else "None"

        # 结果容器与用量统计
        inference_results: List[OptimizedSubtitleItem] = []
        total_usage_accumulator = {}

        # =========================================================
        # Stage 1: Batch Role Inference (批量角色推断)
        # =========================================================
        num_batches = math.ceil(total_lines / task_input.batch_size)

        for batch_idx in range(num_batches):
            start_idx = batch_idx * task_input.batch_size
            end_idx = min((batch_idx + 1) * task_input.batch_size, total_lines)
            batch_items = all_lines[start_idx:end_idx]

            self.logger.info(f"Processing Batch {batch_idx + 1}/{num_batches}...")

            # 构造压缩文本：注入声纹性别特征
            lines = []
            for item in batch_items:
                gender_tag = ""
                # [VSS-Edge Upgrade] 检查声纹性别特征
                if getattr(item, 'audio_analysis', None) and \
                        getattr(item.audio_analysis, 'gender', None) and \
                        item.audio_analysis.gender != "Unknown":
                    gender_tag = f"[Gender: {item.audio_analysis.gender}] "

                # 格式: [序号] [Gender: X] 对白
                lines.append(f"[{item.index}] {gender_tag}{item.content}")

            compressed_text = "\n".join(lines)

            prompt = self._build_prompt(
                prompts_dir=self.prompts_dir,
                prompt_name="role_inference_batch",
                lang=task_input.lang,
                character_list=chars_str,
                video_title=task_input.video_title or "Unknown",
                compressed_subtitles=compressed_text
            )

            try:
                # 调用 Gemini 强类型接口
                response_obj, usage = self.gemini_processor.generate_content(
                    model_name=task_input.model_name,
                    prompt=prompt,
                    response_schema=BatchRoleInferenceResponse,
                    temperature=task_input.temperature
                )
                self._aggregate_usage(total_usage_accumulator, usage)

                # 建立映射表
                speaker_map = {m.index: m.speaker for m in response_obj.mappings}

                # 回填增量结果
                for item in batch_items:
                    inference_results.append(OptimizedSubtitleItem(
                        index=item.index,
                        speaker=speaker_map.get(item.index, "Unknown"),
                        reasoning="AI Inferred"
                    ))

            except Exception as e:
                self.logger.error(f"Batch {batch_idx + 1} failed: {e}")
                # 异常处理：标记为 Unknown (Error)
                for item in batch_items:
                    inference_results.append(OptimizedSubtitleItem(
                        index=item.index,
                        speaker="Unknown (Error)",
                        reasoning=f"Error: {str(e)[:50]}"
                    ))

        # =========================================================
        # Stage 2: Speaker Normalization (名称归一化)
        # =========================================================
        raw_speakers = list(
            set([res.speaker for res in inference_results if res.speaker not in ["Unknown", "Unknown (Error)"]]))

        if len(raw_speakers) >= 2:
            self.logger.info(f"Normalizing {len(raw_speakers)} unique speaker names...")
            norm_map = self._normalize_speakers(
                raw_speakers, task_input.model_name, task_input.lang,
                total_usage_accumulator, task_input.temperature
            )

            for res in inference_results:
                if res.speaker in norm_map:
                    res.speaker = norm_map[res.speaker]

        # =========================================================
        # Stage 3: Post-Processing & Result Delivery
        # =========================================================

        final_stats_obj = UsageStats(model_used=task_input.model_name, **total_usage_accumulator)
        cost_report = self.cost_calculator.calculate(final_stats_obj)

        # 【核心修正】：直接返回一个原始字典给 Handler
        # 此时不要受限于 CharacterPreAnnotatorResult 这个 API 返回契约
        # 因为它此时既包含“要存数据库的数据”，也包含“要存文件的数据”

        return {
            "optimized_subtitles": [item.model_dump() for item in inference_results],
            "stats": {
                "total_lines": total_lines,
                "processed_lines": len(inference_results),
                "batches": num_batches,
            },
            "usage_report": cost_report.to_dict()
        }

    # --- 内部辅助方法 ---

    def _load_subtitle_items(self, path_str: str) -> List[SubtitleInputItem]:
        """[V3.8] 混合路径读取适配器，直接产出 SubtitleInputItem 对象列表"""
        content = ""
        if path_str.startswith("gs://"):
            try:
                parts = path_str[5:].split("/", 1)
                bucket_name, blob_name = parts[0], parts[1]
                client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
                content = client.bucket(bucket_name).blob(blob_name).download_as_text(encoding='utf-8')
            except Exception as e:
                raise BizException(ErrorCode.FILE_IO_ERROR, f"GCS Read Failed: {e}")
        else:
            p = Path(path_str)
            if not p.is_absolute(): p = settings.SHARED_ROOT / p
            if not p.exists(): raise BizException(ErrorCode.FILE_IO_ERROR, f"File not found: {p}")
            content = p.read_text(encoding='utf-8')

        try:
            raw_data = json.loads(content)
            return [SubtitleInputItem(**item) for item in raw_data]
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"JSON Parsing/Validation failed: {e}")

    def _normalize_speakers(self, raw_names: List[str], model: str, lang: str,
                            usage_acc: Dict, temperature: float) -> Dict[str, str]:
        prompt = self._build_prompt(
            prompts_dir=self.prompts_dir, prompt_name="speaker_normalization",
            lang=lang, name_list=json.dumps(raw_names, ensure_ascii=False)
        )
        try:
            response_obj, usage = self.gemini_processor.generate_content(
                model_name=model, prompt=prompt,
                response_schema=SpeakerNormalizationResponse, temperature=temperature
            )
            self._aggregate_usage(usage_acc, usage)
            return {item.original_name: item.normalized_name for item in response_obj.normalization_items}
        except Exception as e:
            self.logger.error(f"Normalization failed: {e}")
            return {}