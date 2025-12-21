import logging
import json
import time
import os
from pathlib import Path
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    ScenePreAnnotatorPayload, ScenePreAnnotatorResult, AnnotatedSliceResult,
    VisualAnalysisOutput, BatchVisualOutput, SceneSegmentationResponse, SliceInput, SceneDefinition
)
from .i18n import get_localized_term
from django.conf import settings

logger = logging.getLogger(__name__)


class ScenePreAnnotatorService(AIServiceMixin):
    SERVICE_NAME = "scene_pre_annotator"

    MAX_WORKERS = 4
    VISUAL_BATCH_SIZE = 15
    SEMANTIC_BATCH_SIZE = 500

    # [文件持久化] 分别存储结果和消耗，确保断点续传时成本统计不丢失
    RESULT_CACHE_FILE = "visual_inference_result_checkpoint.json"
    USAGE_CACHE_FILE = "visual_inference_usage_checkpoint.json"

    def __init__(self, logger: logging.Logger, gemini_processor: GeminiProcessor, cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

        self.result_cache_path = Path(__file__).parent / self.RESULT_CACHE_FILE
        self.usage_cache_path = Path(__file__).parent / self.USAGE_CACHE_FILE

        try:
            self.project_id = getattr(settings, 'GOOGLE_CLOUD_PROJECT', os.getenv("GOOGLE_CLOUD_PROJECT"))
            self.location = getattr(settings, 'GOOGLE_CLOUD_LOCATION',
                                    os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
            if self.project_id:
                self.vertex_client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
            else:
                self.vertex_client = None
        except Exception as e:
            self.logger.error(f"Failed to init Google GenAI Client: {e}")
            self.vertex_client = None

    def _load_checkpoints(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """加载结果和Usage缓存"""
        results = {}
        usages = {}
        if self.result_cache_path.exists():
            try:
                with open(self.result_cache_path, 'r', encoding='utf-8') as f:
                    results = json.load(f)
            except:
                pass

        if self.usage_cache_path.exists():
            try:
                with open(self.usage_cache_path, 'r', encoding='utf-8') as f:
                    usages = json.load(f)
            except:
                pass

        return results, usages

    def _save_checkpoints(self, results: Dict[str, Any], usages: Dict[str, Any]):
        """双写缓存"""
        try:
            with open(self.result_cache_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False)
            with open(self.usage_cache_path, 'w', encoding='utf-8') as f:
                json.dump(usages, f, ensure_ascii=False)
        except Exception as e:
            self.logger.warning(f"Failed to save checkpoints: {e}")

    def _batch_visual_inference(self, slices: List[SliceInput], lang: str, model_name: str, video_title: str) -> Dict[
        int, VisualAnalysisOutput]:
        if not self.vertex_client: return {}, {}
        valid_slices = [s for s in slices if s.frames]
        if not valid_slices: return {}, {}

        prompt_tpl = self._load_prompt_template(self.prompts_dir, lang, "visual_inference")
        instruction = prompt_tpl.format(video_title=video_title)

        contents = [instruction]
        for s in valid_slices:
            contents.append(f"\n--- Slice ID: {s.slice_id} ---")
            has_valid_frame = False
            for f in s.frames:
                if f.path and f.path.startswith("gs://"):
                    contents.append(types.Part.from_uri(file_uri=f.path, mime_type="image/jpeg"))
                    has_valid_frame = True
            if not has_valid_frame: contents.append("[Missing Remote Frame Data]")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                clean_model = model_name.replace("models/", "")
                response = self.vertex_client.models.generate_content(
                    model=clean_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                        response_schema=BatchVisualOutput,
                    )
                )

                if not response.parsed: raise ValueError(f"SDK failed to parse response: {response.text}")
                batch_output: BatchVisualOutput = response.parsed

                result_map = {}
                if batch_output.results:
                    for res_item in batch_output.results:
                        result_map[res_item.slice_id] = res_item

                # [Fix] 显式添加 successful_requests
                usage_dict = {
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "successful_requests": 1  # <--- 修复点：标记为一次成功请求
                }
                if response.usage_metadata:
                    usage_dict.update({
                        "prompt_tokens": response.usage_metadata.prompt_token_count,
                        "completion_tokens": response.usage_metadata.candidates_token_count,
                        "total_tokens": response.usage_metadata.total_token_count
                    })

                return result_map, usage_dict

            except Exception as e:
                self.logger.warning(f"Unified SDK Inference failed (Attempt {attempt + 1}): {e}")
                if "429" in str(e):
                    time.sleep(10 * (attempt + 1))
                else:
                    time.sleep(2 * (attempt + 1))

        return {}, {}

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Scene Pre-Annotation (V3.7 Process Control)...")

        try:
            task_input = ScenePreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        total_usage_accumulator = {}
        annotated_slices: List[AnnotatedSliceResult] = []

        # =====================================================
        # Stage 1: 视觉推理 (带结果和Usage双重断点续传)
        # =====================================================
        if task_input.injected_annotated_slices:
            self.logger.info(f"⚡ CACHE HIT: Using {len(task_input.injected_annotated_slices)} injected slices.")
            annotated_slices = task_input.injected_annotated_slices
        else:
            total_slices = len(task_input.slices)

            # 1. 加载双重断点
            ckpt_results_raw, ckpt_usages_raw = self._load_checkpoints()

            visual_results_map = {}
            # 恢复结果
            for k, v in ckpt_results_raw.items():
                try:
                    visual_results_map[int(k)] = VisualAnalysisOutput(**v)
                except:
                    pass

            # [关键] 恢复之前的 Usage，确保断点续传时成本不归零
            # 我们将 usage 存储为 "batch_index": usage_dict 的形式，或者简单累加？
            # 简单策略：遍历 ckpt_usages_raw (key=slice_id_start, value=usage_dict) 并累加
            for _, u_dict in ckpt_usages_raw.items():
                self._aggregate_usage(total_usage_accumulator, u_dict)

            if len(visual_results_map) > 0:
                self.logger.info(
                    f"🔄 Resuming: {len(visual_results_map)}/{total_slices} slices done. Accumulated Cost recovered.")

            slices_to_process = [s for s in task_input.slices if s.slice_id not in visual_results_map]

            if slices_to_process:
                self.logger.info(f"--- Stage 1: Processing {len(slices_to_process)} remote slices ---")

                chunks = [slices_to_process[i:i + self.VISUAL_BATCH_SIZE] for i in
                          range(0, len(slices_to_process), self.VISUAL_BATCH_SIZE)]

                with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                    future_to_chunk = {
                        executor.submit(
                            self._batch_visual_inference,
                            chunk, task_input.lang, task_input.visual_model, task_input.video_title
                        ): chunk for chunk in chunks
                    }

                    completed_batches = 0
                    for future in as_completed(future_to_chunk):
                        chunk_data = future_to_chunk[future]
                        try:
                            batch_res, batch_usage = future.result()
                            visual_results_map.update(batch_res)
                            self._aggregate_usage(total_usage_accumulator, batch_usage)

                            # Update Checkpoints
                            for k, v in batch_res.items():
                                ckpt_results_raw[str(k)] = v.model_dump()

                            # Usage 记录：为了防止重复累加，我们以该 Batch 的第一个 Slice ID 为 Key 存储 Usage
                            if batch_res:
                                first_id = str(list(batch_res.keys())[0])
                                ckpt_usages_raw[first_id] = batch_usage

                            self._save_checkpoints(ckpt_results_raw, ckpt_usages_raw)

                            completed_batches += 1
                            self.logger.info(f"✅ Batch {completed_batches}/{len(chunks)} Completed.")
                        except Exception as exc:
                            self.logger.error(f"❌ Inference Exception: {exc}")

            # 组装
            for slice_item in task_input.slices:
                vis_res = visual_results_map.get(slice_item.slice_id)
                annotated_item = AnnotatedSliceResult(
                    slice_id=slice_item.slice_id,
                    start_time=slice_item.start_time,
                    end_time=slice_item.end_time,
                    type=slice_item.type,
                    text_content=slice_item.text_content,
                    visual_analysis=vis_res
                )
                annotated_slices.append(annotated_item)

        # =====================================================
        # Stage 2: 语义重组 (Gemini API)
        # =====================================================
        # (这部分代码保持不变，请直接使用 V3.6 的 Stage 2 代码)
        self.logger.info(f"--- Stage 2: Semantic Grouping (Chunk Size={self.SEMANTIC_BATCH_SIZE}) ---")

        target_lang = task_input.lang
        all_scenes = []
        global_scene_index = 1
        semantic_chunks = [annotated_slices[i:i + self.SEMANTIC_BATCH_SIZE] for i in
                           range(0, len(annotated_slices), self.SEMANTIC_BATCH_SIZE)]

        for i, chunk_slices in enumerate(semantic_chunks):
            self.logger.info(f"Processing Semantic Chunk {i + 1}/{len(semantic_chunks)}...")
            slice_log_lines = []
            for s in chunk_slices:
                time_str = f"({s.start_time:.1f}s-{s.end_time:.1f}s)"
                text_part = f"📖[SUB]: {s.text_content.replace(chr(10), ' ').strip()}" if s.text_content else "🔇[NO_TEXT]"
                vis_part = ""
                if s.visual_analysis:
                    v = s.visual_analysis
                    shot_str = get_localized_term(v.shot_type, target_lang)
                    mood_str = get_localized_term(v.mood, target_lang)
                    vis_part = f"📷[VIS]: {shot_str} | {v.subject} | {v.action} | {mood_str}"
                line = f"[Slice {s.slice_id}] {time_str} {text_part} {vis_part}"
                slice_log_lines.append(line)
            full_log_text = "\n".join(slice_log_lines)

            prompt = self._build_prompt(self.prompts_dir, "scene_segmentation", task_input.lang,
                                        slice_log=full_log_text)
            try:
                seg_resp, seg_usage = self.gemini_processor.generate_content(
                    model_name=task_input.text_model,
                    prompt=prompt,
                    response_schema=SceneSegmentationResponse,
                    temperature=0.1
                )
                self._aggregate_usage(total_usage_accumulator, seg_usage)
                if seg_resp and seg_resp.scenes:
                    for scene in seg_resp.scenes:
                        scene.index = global_scene_index
                        global_scene_index += 1
                        all_scenes.append(scene)
            except Exception as e:
                self.logger.error(f"Stage 2 failed for chunk {i + 1}: {e}")

        # Report
        pricing_model = task_input.visual_model
        final_stats_obj = UsageStats(model_used=pricing_model, **total_usage_accumulator)
        cost_report = self.cost_calculator.calculate(final_stats_obj)

        result = ScenePreAnnotatorResult(
            scenes=all_scenes,
            annotated_slices=annotated_slices,
            stats={"total_input_slices": len(task_input.slices), "generated_scenes": len(all_scenes)},
            usage_report=cost_report.to_dict()
        )
        return result.model_dump()