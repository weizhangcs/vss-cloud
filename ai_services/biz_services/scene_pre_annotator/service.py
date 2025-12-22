# ai_services/biz_services/scene_pre_annotator/service.py

import json
import time
import os
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from google import genai
from google.genai import types
from google.cloud import storage  # [V3.8] 新增：用于读取 GCS 上的切片文件

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from configuration.tag_manager import TagManager

from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    ScenePreAnnotatorPayload, ScenePreAnnotatorResult, AnnotatedSliceResult,
    VisualAnalysisOutput, BatchVisualOutput, SceneSegmentationResponse, SliceInput
)
from .i18n import get_localized_term

logger = logging.getLogger(__name__)


class ScenePreAnnotatorService(AIServiceMixin):
    SERVICE_NAME = "scene_pre_annotator"

    MAX_WORKERS = 4
    VISUAL_BATCH_SIZE = 20
    SEMANTIC_BATCH_SIZE = 250

    # 文件持久化配置
    RESULT_CACHE_FILE = "visual_inference_result_checkpoint.json"
    USAGE_CACHE_FILE = "visual_inference_usage_checkpoint.json"

    def __init__(self, logger: logging.Logger, gemini_processor: GeminiProcessor, cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

        try:
            self.project_id = getattr(settings, 'GOOGLE_CLOUD_PROJECT', None)
            self.location = getattr(settings, 'GOOGLE_CLOUD_LOCATION', "us-central1")
            if self.project_id:
                # vertexai=True 支持 GCS URI
                self.vertex_client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
            else:
                self.vertex_client = None
        except Exception as e:
            self.logger.error(f"Failed to init Google GenAI Client: {e}")
            self.vertex_client = None

    # --- 断点续传核心方法 ---

    def _get_cache_paths(self, task_id: str = "default") -> Tuple[Path, Path]:
        cache_dir = settings.SHARED_TMP_ROOT / f"scene_annotator_{task_id}_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return (cache_dir / self.RESULT_CACHE_FILE), (cache_dir / self.USAGE_CACHE_FILE)

    def _load_checkpoints(self, result_path: Path, usage_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        results = {}
        usages = {}

        # 加载结果缓存 (带异常保护)
        if result_path.exists():
            try:
                results = json.loads(result_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as e:
                self.logger.warning(
                    f"⚠️ Corrupted result checkpoint at {result_path}: {e}. Starting with empty results.")
            except OSError as e:
                self.logger.error(f"❌ IO Error reading result checkpoint {result_path}: {e}")

        # 加载用量缓存 (带异常保护)
        if usage_path.exists():
            try:
                usages = json.loads(usage_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as e:
                self.logger.warning(f"⚠️ Corrupted usage checkpoint at {usage_path}: {e}. Resetting usage stats.")
            except OSError as e:
                self.logger.error(f"❌ IO Error reading usage checkpoint {usage_path}: {e}")

        return results, usages

    def _save_checkpoints(self, result_path: Path, usage_path: Path, results: Dict, usages: Dict):
        try:
            result_path.write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')
            usage_path.write_text(json.dumps(usages, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            self.logger.warning(f"Failed to save checkpoints: {e}")

    # --- 核心辅助方法：文件读取 ---

    def _read_file_content(self, path_str: str) -> str:
        """
        [V3.8 New] 读取文件内容 (支持 gs:// 和 本地路径)
        用于加载 slices_file_path 指向的大型 JSON 文件
        """
        if path_str.startswith("gs://"):
            try:
                # 解析 gs://bucket/path
                parts = path_str[5:].split("/", 1)
                bucket_name = parts[0]
                blob_name = parts[1]

                # 初始化 Storage Client (复用项目 ID)
                client = storage.Client(project=self.project_id)
                bucket = client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                return blob.download_as_text(encoding='utf-8')
            except Exception as e:
                self.logger.error(f"Failed to read GCS file {path_str}: {e}")
                raise BizException(ErrorCode.FILE_IO_ERROR, f"GCS Read Failed: {e}")
        else:
            # 本地路径处理
            p = Path(path_str)
            if not p.is_absolute():
                p = settings.SHARED_ROOT / p

            if not p.exists():
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Local file not found: {p}")

            return p.read_text(encoding='utf-8')

    # -----------------------------------------------------------

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Scene Pre-Annotation (V3.8 Large Payload Support)...")

        # 1. 校验 Schema
        try:
            task_input = ScenePreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        # 2. [V3.8] 数据加载策略 (Pass-by-Reference)
        target_slices: List[SliceInput] = []

        if task_input.slices:
            # 兼容模式：直接从 Payload 获取
            target_slices = task_input.slices
            self.logger.info(f"Loaded {len(target_slices)} slices from Payload.")
        elif task_input.slices_file_path:
            # 高性能模式：从文件加载
            self.logger.info(f"Loading slices from external file: {task_input.slices_file_path}")
            try:
                json_content = self._read_file_content(task_input.slices_file_path)
                raw_list = json.loads(json_content)

                # 再次利用 Pydantic 校验加载的数据
                if not isinstance(raw_list, list):
                    raise ValueError("File content must be a JSON array of slices.")

                target_slices = [SliceInput(**item) for item in raw_list]
                self.logger.info(f"✅ Successfully loaded {len(target_slices)} slices from file.")
            except Exception as e:
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Failed to load slices file: {e}")
        else:
            # 理论上 Schema 校验会拦截，但防御性编程
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR,
                               "No slices data provided (neither 'slices' nor 'slices_file_path').")

        # 缓存键计算
        cache_key = str(abs(hash(task_input.video_title)))
        result_cache_path, usage_cache_path = self._get_cache_paths(cache_key)

        total_usage_accumulator = {}
        annotated_slices: List[AnnotatedSliceResult] = []

        # =====================================================
        # Stage 1: 视觉推理 (Visual Inference)
        # =====================================================
        if task_input.injected_annotated_slices:
            self.logger.info(f"⚡ CACHE HIT: Using {len(task_input.injected_annotated_slices)} injected slices.")
            annotated_slices = task_input.injected_annotated_slices
        else:
            # 1. 加载断点
            ckpt_results_raw, ckpt_usages_raw = self._load_checkpoints(result_cache_path, usage_cache_path)

            visual_results_map = {}
            # 恢复结果 (带异常保护)
            for k, v in ckpt_results_raw.items():
                try:
                    visual_results_map[int(k)] = VisualAnalysisOutput(**v)
                except Exception:
                    # 忽略无法恢复的单条记录
                    pass

            # 恢复用量
            for _, u_dict in ckpt_usages_raw.items():
                self._aggregate_usage(total_usage_accumulator, u_dict)

            if len(visual_results_map) > 0:
                self.logger.info(f"🔄 Resuming: {len(visual_results_map)} slices loaded from cache.")

            # 2. 筛选未处理切片 (使用 target_slices)
            slices_to_process = [s for s in target_slices if s.slice_id not in visual_results_map]

            if slices_to_process:
                self.logger.info(f"--- Stage 1: Processing {len(slices_to_process)} remaining slices ---")

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
                        try:
                            batch_res, batch_usage = future.result()

                            # 更新内存 & 累加用量
                            visual_results_map.update(batch_res)
                            self._aggregate_usage(total_usage_accumulator, batch_usage)

                            # 更新断点数据
                            for k, v in batch_res.items():
                                ckpt_results_raw[str(k)] = v.model_dump()

                            if batch_res:
                                first_id = str(list(batch_res.keys())[0])
                                ckpt_usages_raw[first_id] = batch_usage

                            # 保存断点
                            self._save_checkpoints(result_cache_path, usage_cache_path, ckpt_results_raw,
                                                   ckpt_usages_raw)

                            completed_batches += 1
                            if completed_batches % 5 == 0:
                                self.logger.info(f"✅ Progress: {completed_batches}/{len(chunks)} batches.")

                        except Exception as exc:
                            self.logger.error(f"❌ Batch Inference Exception: {exc}")

            # 3. 组装结果 (使用 target_slices)
            for slice_item in target_slices:
                vis_res = visual_results_map.get(slice_item.slice_id)

                # [New Logic] 接入 TagManager 进行归一化 (V4.1)
                if vis_res and vis_res.visual_mood_tags:
                    try:
                        # 无论 LLM 返回 "温馨", "Warm" 还是 "Cozy"，都清洗为 Canonical Key "warm"
                        normalized_tags = TagManager.normalize_tags(
                            vis_res.visual_mood_tags,
                            category='visual_mood',
                            auto_add_unknown=True
                        )
                        vis_res.visual_mood_tags = normalized_tags
                    except Exception as e:
                        self.logger.warning(f"Tag normalization failed for slice {slice_item.slice_id}: {e}")

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
        # Stage 2: 语义重组 (Semantic Segmentation)
        # =====================================================
        self.logger.info(f"--- Stage 2: Semantic Grouping (Chunk Size={self.SEMANTIC_BATCH_SIZE}) ---")

        all_scenes = []
        global_scene_index = 1
        semantic_chunks = [annotated_slices[i:i + self.SEMANTIC_BATCH_SIZE] for i in
                           range(0, len(annotated_slices), self.SEMANTIC_BATCH_SIZE)]

        for i, chunk_slices in enumerate(semantic_chunks):
            self.logger.info(f"Processing Semantic Chunk {i + 1}/{len(semantic_chunks)}...")

            slice_log_lines = []
            for s in chunk_slices:
                # ... (Slice Log 拼接逻辑保持不变) ...
                time_str = f"({s.start_time:.1f}s-{s.end_time:.1f}s)"
                text_content = s.text_content.replace('\n', ' ').strip() if s.text_content else ""
                text_part = f"📖[SUB]: {text_content}" if text_content else "🔇[NO_TEXT]"

                vis_part = ""
                if s.visual_analysis:
                    v = s.visual_analysis
                    shot_str = get_localized_term(v.shot_type, task_input.lang)
                    tags_str = ", ".join(v.visual_mood_tags) if v.visual_mood_tags else "neutral"
                    vis_part = f"📷[VIS]: {shot_str} | {v.subject} | {v.action} | Tags:[{tags_str}]"

                line = f"[Slice {s.slice_id}] {time_str} {text_part} {vis_part}"
                slice_log_lines.append(line)

            full_log_text = "\n".join(slice_log_lines)

            # [CRITICAL FIX] 必须注入 Prompt 所需的新变量
            prompt = self._build_prompt(
                prompts_dir=self.prompts_dir,
                prompt_name="scene_segmentation",
                lang=task_input.lang,
                # --- 新增参数开始 ---
                asset_type=task_input.asset_type,
                content_genre=task_input.content_genre,
                # --- 新增参数结束 ---
                slice_log=full_log_text
            )

            try:
                seg_resp, seg_usage = self.gemini_processor.generate_content(
                    model_name=task_input.text_model,
                    prompt=prompt,
                    # 这里引用的 SceneSegmentationResponse 会自动使用新的 Schema (Location/SceneType等)
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

        # Final Report
        final_stats_obj = UsageStats(model_used=task_input.visual_model, **total_usage_accumulator)
        cost_report = self.cost_calculator.calculate(final_stats_obj)

        result = ScenePreAnnotatorResult(
            scenes=all_scenes,
            annotated_slices=annotated_slices,
            stats={"total_input_slices": len(target_slices), "generated_scenes": len(all_scenes)},
            usage_report=cost_report.to_dict()
        )
        return result.model_dump()

    # --- 核心辅助方法：混合路径加载 ---

    def _batch_visual_inference(self, slices: List[SliceInput], lang: str, model_name: str, video_title: str) -> tuple[
        Dict[int, VisualAnalysisOutput], Dict]:
        if not self.vertex_client:
            return {}, {}

        valid_slices = [s for s in slices if s.frames]
        if not valid_slices:
            return {}, {}

        prompt_tpl = self._load_prompt_template(self.prompts_dir, lang, "visual_inference")
        instruction = prompt_tpl.format(video_title=video_title)

        # 显式声明列表类型以消除警告
        contents: List[Union[str, types.Part]] = [instruction]

        for s in valid_slices:
            contents.append(f"\n--- Slice ID: {s.slice_id} ---")
            has_valid_frame = False
            for f in s.frames:
                try:
                    # 调用统一加载器
                    part = self._load_image_part(f.path)
                    if part:
                        contents.append(part)
                        has_valid_frame = True
                except Exception as e:
                    self.logger.warning(f"Skipping frame {f.path}: {e}")

            if not has_valid_frame:
                contents.append("[Missing Frame Data]")

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

            if not response.parsed:
                raise ValueError(f"SDK failed to parse response: {response.text}")

            batch_output: BatchVisualOutput = response.parsed
            result_map = {}
            if batch_output and batch_output.results:
                for res_item in batch_output.results:
                    result_map[res_item.slice_id] = res_item

            # 使用标准化方法提取 Usage
            usage_dict = self.gemini_processor.normalize_usage(response)

            return result_map, usage_dict

        except Exception as e:
            self.logger.error(f"Batch inference failed: {e}")
            return {}, {}

    def _load_image_part(self, path_str: str) -> Optional[types.Part]:
        """
        统一图片加载器 (支持 gs:// 和 local)
        """
        if path_str.startswith("gs://"):
            return types.Part.from_uri(file_uri=path_str, mime_type="image/jpeg")
        else:
            # 本地回归测试模式
            local_path = Path(path_str)
            if not local_path.is_absolute():
                local_path = settings.SHARED_ROOT / local_path

            if local_path.exists():
                image_bytes = local_path.read_bytes()
                # [Fix] 使用 from_bytes 替代 from_data
                return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            else:
                self.logger.warning(f"Local image frame not found: {local_path}")
                return None