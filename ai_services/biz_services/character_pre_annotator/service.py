# ai_services/biz_services/character_pre_annotator/service.py

import json
import math
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Union

from django.conf import settings
from google.cloud import storage  # [New Requirement]

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    CharacterPreAnnotatorPayload, CharacterPreAnnotatorResult,
    OptimizedSubtitleItem, BatchRoleInferenceResponse, SpeakerNormalizationResponse
)

logger = logging.getLogger(__name__)


class SubtitleLine:
    def __init__(self, index, start, end, content):
        self.index = index
        self.start_time = start
        self.end_time = end
        self.content = content


class CharacterPreAnnotatorService(AIServiceMixin):
    SERVICE_NAME = "character_pre_annotator"
    DEFAULT_BATCH_SIZE = 150
    DEFAULT_TEMPERATURE = 0.1

    def __init__(self,
                 logger: logging.Logger,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Character Pre-Annotation (Hybrid Cloud Mode)...")

        # 1. 校验输入
        try:
            task_input = CharacterPreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        # 2. 读取 SRT 内容 (支持 本地/GCS)
        raw_srt_content = self._read_subtitle_content(task_input.subtitle_path)

        all_lines = self._parse_srt(raw_srt_content)
        total_lines = len(all_lines)

        batch_size = task_input.batch_size
        temperature = task_input.temperature

        self.logger.info(
            f"Parsed {total_lines} lines from {task_input.subtitle_path}. Strategy: Batch Processing (Size={batch_size})")

        # 3. 准备上下文
        chars_str = ", ".join(task_input.known_characters) if task_input.known_characters else "None"

        final_results = []
        total_usage_accumulator = {}

        # =========================================================
        # Stage 1: Batch Role Inference
        # =========================================================
        num_batches = math.ceil(total_lines / batch_size)

        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min((batch_idx + 1) * batch_size, total_lines)
            batch_lines = all_lines[start_idx:end_idx]

            self.logger.info(f"Processing Batch {batch_idx + 1}/{num_batches}...")

            compressed_text = "\n".join([f"{line.index} {line.content}" for line in batch_lines])

            prompt = self._build_prompt(
                prompts_dir=self.prompts_dir,
                prompt_name="role_inference_batch",
                lang=task_input.lang,
                character_list=chars_str,
                video_title=task_input.video_title or "Unknown",
                compressed_subtitles=compressed_text
            )

            try:
                response_obj, usage = self.gemini_processor.generate_content(
                    model_name=task_input.model_name,
                    prompt=prompt,
                    response_schema=BatchRoleInferenceResponse,
                    temperature=temperature
                )

                self._aggregate_usage(total_usage_accumulator, usage)

                speaker_map = {m.index: m.speaker for m in response_obj.mappings}

                for line in batch_lines:
                    speaker = speaker_map.get(line.index, "Unknown")
                    final_results.append(OptimizedSubtitleItem(
                        index=line.index,
                        start_time=self._srt_time_to_seconds(line.start_time),
                        end_time=self._srt_time_to_seconds(line.end_time),
                        content=line.content,
                        speaker=speaker,
                        reasoning="Batch Inferred"
                    ))

            except Exception as e:
                self.logger.error(f"Batch {batch_idx + 1} failed: {e}")
                for line in batch_lines:
                    final_results.append(OptimizedSubtitleItem(
                        index=line.index,
                        start_time=self._srt_time_to_seconds(line.start_time),
                        end_time=self._srt_time_to_seconds(line.end_time),
                        content=line.content,
                        speaker="Unknown (Error)",
                        reasoning=f"Error: {str(e)[:50]}"
                    ))

        # =========================================================
        # Stage 2: Speaker Normalization
        # =========================================================
        self.logger.info("Stage 2: Normalizing Speaker Names...")

        raw_speakers = list(set([item.speaker for item in final_results if item.speaker != "Unknown"]))

        if len(raw_speakers) >= 2:
            norm_map = self._normalize_speakers(
                raw_speakers,
                task_input.model_name,
                task_input.lang,
                total_usage_accumulator,
                temperature,
            )

            update_count = 0
            for item in final_results:
                if item.speaker in norm_map:
                    new_name = norm_map[item.speaker]
                    if item.speaker != new_name:
                        item.speaker = new_name
                        update_count += 1
            self.logger.info(f"Normalized {update_count} lines.")

        # =========================================================
        # Stage 3: Post Processing
        # =========================================================
        # [Note] ASS 生成逻辑暂时仅支持本地写出，如果是 GCS 路径，这里生成在 TMP 目录
        output_ass_path = self._generate_ass_file(task_input.subtitle_path, final_results)
        metrics_report = self._calculate_metrics(final_results)

        final_stats_obj = UsageStats(model_used=task_input.model_name, **total_usage_accumulator)
        cost_report = self.cost_calculator.calculate(final_stats_obj)

        result = CharacterPreAnnotatorResult(
            input_file=task_input.subtitle_path,
            optimized_subtitles=final_results,
            output_ass_path=str(output_ass_path) if output_ass_path else None,
            character_roster=metrics_report.get("character_roster", []),
            stats={
                "total_lines": total_lines,
                "processed_lines": len(final_results),
                "batches": num_batches,
                "unique_characters": len(metrics_report.get("character_roster", []))
            },
            usage_report=cost_report.to_dict()
        )

        return result.model_dump()

    # --- 辅助方法 ---

    def _read_subtitle_content(self, path_str: str) -> str:
        """
        [Core Upgrade] 混合路径读取适配器
        """
        if path_str.startswith("gs://"):
            # GCS 路径读取
            try:
                # gs://bucket_name/path/to/file
                parts = path_str[5:].split("/", 1)
                bucket_name = parts[0]
                blob_name = parts[1]

                client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
                bucket = client.bucket(bucket_name)
                blob = bucket.blob(blob_name)

                # 下载为字符串 (Assuming UTF-8)
                content = blob.download_as_text(encoding='utf-8')
                return content
            except Exception as e:
                self.logger.error(f"Failed to read GCS file {path_str}: {e}")
                raise BizException(ErrorCode.FILE_IO_ERROR, f"GCS Read Failed: {e}")
        else:
            # 本地绝对路径 (Regression Test Mode)
            p = Path(path_str)
            # 如果是相对路径，尝试 resolve 到 SHARED_ROOT
            if not p.is_absolute():
                p = settings.SHARED_ROOT / p

            if not p.exists():
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Local file not found: {p}")

            return p.read_text(encoding='utf-8-sig')

    def _generate_ass_file(self, original_path_str: str, items: List[OptimizedSubtitleItem]) -> Path:
        """生成 ASS 文件到临时目录 (不再试图写回 GCS 源目录)"""
        # 使用文件名作为标识
        orig_name = Path(original_path_str).name
        output_filename = f"{Path(orig_name).stem}_ai_labeled.ass"
        # 始终写入到 SHARED_TMP_ROOT
        output_path = settings.SHARED_TMP_ROOT / output_filename

        def sec_to_ass_time(seconds: float) -> str:
            total_sec = int(seconds)
            cs = int((seconds - total_sec) * 100)
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        header = """[Script Info]
Title: VSS AI Generated Subtitle
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for item in items:
            start_str = sec_to_ass_time(item.start_time)
            end_str = sec_to_ass_time(item.end_time)
            safe_speaker = item.speaker.replace(",", " ").strip() if item.speaker else "Unknown"
            safe_content = item.content.replace("\n", "\\N")
            line = f"Dialogue: 0,{start_str},{end_str},Default,{safe_speaker},0,0,0,,{safe_content}"
            events.append(line)

        full_content = header + "\n".join(events)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        try:
            return output_path.relative_to(settings.SHARED_ROOT)
        except:
            return output_path

    def _normalize_speakers(self, raw_names: List[str], model: str, lang: str, usage_acc: Dict, temperature: float) -> Dict[str, str]:
        """使用 AI 进行名字归一化"""
        names_str = json.dumps(raw_names, indent=2, ensure_ascii=False)

        prompt = self._build_prompt(
            prompts_dir=self.prompts_dir,
            prompt_name="speaker_normalization",
            lang=lang,
            name_list=names_str
        )

        try:
            response_obj, usage = self.gemini_processor.generate_content(
                model_name=model,
                prompt=prompt,
                response_schema=SpeakerNormalizationResponse,
                temperature=temperature
            )
            self._aggregate_usage(usage_acc, usage)

            # [Fix Issue 1] Convert List[Item] to Dict
            return {item.original_name: item.normalized_name for item in response_obj.normalization_items}

        except Exception as e:
            self.logger.error(f"Normalization failed: {e}")
            return {}

    def _parse_srt(self, content: str) -> List[SubtitleLine]:
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        blocks = content.strip().split('\n\n')
        parsed_lines = []
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                try:
                    idx = int(lines[0].strip())
                    start, end = lines[1].split(' --> ')
                    text = " ".join(lines[2:]).strip()
                    parsed_lines.append(SubtitleLine(idx, start.strip(), end.strip(), text))
                except:
                    pass
        return parsed_lines

    def _srt_time_to_seconds(self, time_str: str) -> float:
        if not time_str: return 0.0
        try:
            time_str = time_str.replace(',', '.')
            h, m, s = time_str.split(':')
            return float(h) * 3600 + float(m) * 60 + float(s)
        except:
            return 0.0



    def _calculate_metrics(self, items: List[OptimizedSubtitleItem]) -> Dict:
        """
        [Fix Issue 2] 修复权重计算
        """
        metrics = defaultdict(lambda: {"count": 0, "duration": 0.0, "raw": set()})
        for item in items:
            # 排除非角色
            if item.speaker in ["Unknown", "Unknown (Error)"]: continue
            key = item.speaker
            metrics[key]["count"] += 1
            metrics[key]["duration"] += (item.end_time - item.start_time)
            metrics[key]["raw"].add(item.speaker)

        roster = []
        # 安全获取最大值
        max_lines = max((v["count"] for v in metrics.values()), default=1)

        for k, v in metrics.items():
            # 简单权重：行数越多权重越高
            score = v["count"]

            roster.append({
                "name": k,
                "key": k,
                "weight_score": score,
                "_raw_score": score,  # [Fix] 必须保留原始分用于计算百分比
                "weight_percent": "0%",  # 占位
                "stats": {"lines": v["count"], "duration_sec": round(v["duration"], 2)},
                "variations": list(v["raw"])
            })

        # 计算百分比
        roster.sort(key=lambda x: x["weight_score"], reverse=True)
        top_score = roster[0]["_raw_score"] if roster else 1

        for r in roster:
            pct = (r["_raw_score"] / top_score) * 100 if top_score > 0 else 0
            r["weight_percent"] = f"{round(pct, 1)}%"
            del r["_raw_score"]  # 清理临时字段

        return {"character_roster": roster}