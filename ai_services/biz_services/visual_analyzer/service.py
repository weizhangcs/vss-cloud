import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from google import genai
from google.genai import types
from google.cloud import storage

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats
from configuration.tag_manager import TagManager
from core.exceptions import BizException
from core.error_codes import ErrorCode

# 使用新的原子化 Schema
from ai_services.biz_services.visual_analyzer.schemas import (
    VisualAnalyzerPayload, VisualFrameInput, BatchVisualOutput, FrameAnalysisResult, VisualAnalysisData,
    SHOT_TYPE_LABELS
)

logger = logging.getLogger(__name__)

class VisualAnalyzerService(AIServiceMixin):
    SERVICE_NAME = "visual_analyzer"
    
    # 配置
    MAX_WORKERS = 5
    BATCH_SIZE = 20
    RESULT_CACHE_FILE = "visual_analyzer_result.json"
    USAGE_CACHE_FILE = "visual_analyzer_usage.json"

    def __init__(self, logger: logging.Logger, gemini_processor: GeminiProcessor, cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

        try:
            self.project_id = getattr(settings, 'GOOGLE_CLOUD_PROJECT', None)
            self.location = getattr(settings, 'GOOGLE_CLOUD_LOCATION', "us-central1")
            if self.project_id:
                self.vertex_client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
            else:
                self.vertex_client = None
        except Exception as e:
            self.logger.error(f"Failed to init Google GenAI Client: {e}")
            self.vertex_client = None

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Visual Analyzer...")

        # 1. 解析输入
        try:
            task_input = VisualAnalyzerPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        target_frames = self._load_frames(task_input)
        if not target_frames:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, "No frames provided.")

        # 2. 准备缓存路径
        # 使用 第一个 frame_id 作为缓存键源
        cache_key_source = target_frames[0].frame_id if target_frames else "default"
        cache_key = str(abs(hash(cache_key_source)))
        cache_dir = settings.SHARED_TMP_ROOT / "visual_analyzer_cache" / f"{cache_key}"
        cache_dir.mkdir(parents=True, exist_ok=True)

        result_cache_path = cache_dir / self.RESULT_CACHE_FILE
        usage_cache_path = cache_dir / self.USAGE_CACHE_FILE

        # 3. 执行分析
        annotated_frames, usage_accumulator = self._process_visuals(
            target_frames, task_input, result_cache_path, usage_cache_path
        )

        # 4. 计算成本并返回
        cost_report = self.cost_calculator.calculate(
            UsageStats(model_used=task_input.visual_model, **usage_accumulator))

        # 5. 将 ShotType 枚举值转换为对应语言的 Label
        final_frames_output = []
        # 获取对应语言的映射表，默认回退到英文
        label_map = SHOT_TYPE_LABELS.get(task_input.lang, SHOT_TYPE_LABELS.get('en', {}))

        for frame_res in annotated_frames:
            frame_dict = frame_res.model_dump()

            # 如果存在 shot_type 枚举值，则进行替换
            if frame_res.visual_analysis and frame_res.visual_analysis.shot_type:
                enum_val = frame_res.visual_analysis.shot_type
                if enum_val in label_map:
                    frame_dict['visual_analysis']['shot_type'] = label_map[enum_val]

            final_frames_output.append(frame_dict)

        return {
            "annotated_frames": final_frames_output,
            "stats": {"total_frames": len(target_frames), "processed": len(annotated_frames)},
            "usage_report": cost_report.to_dict()
        }

    def _process_visuals(self, frames: List[VisualFrameInput], task_input: VisualAnalyzerPayload, result_path: Path,
                         usage_path: Path):
        # 加载断点
        results_map = {}
        if result_path.exists():
            try:
                raw = json.loads(result_path.read_text(encoding='utf-8'))
                for k, v in raw.items():
                    # frame_id 是 str
                    results_map[k] = VisualAnalysisData(**v)
            except Exception:
                pass

        usage_acc = {}

        # 筛选
        to_process = [f for f in frames if f.frame_id not in results_map]

        if to_process:
            # [优化] 提前加载 Prompt，避免在每个 Batch 中重复读取文件
            lang = task_input.lang
            try:
                prompt_path = self.prompts_dir / f"visual_inference_{lang}.txt"
                if not prompt_path.exists():
                    self.logger.warning(f"⚠️ Prompt file not found: {prompt_path}. Falling back to English.")
                    prompt_path = self.prompts_dir / "visual_inference_en.txt"

                self.logger.info(f"📖 Loading prompt from: {prompt_path}")
                instruction = prompt_path.read_text(encoding='utf-8')
            except Exception as e:
                self.logger.error(f"Failed to load prompt: {e}")
                return [], {}

            chunks = [to_process[i:i + self.BATCH_SIZE] for i in range(0, len(to_process), self.BATCH_SIZE)]

            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                future_to_chunk = {
                    executor.submit(self._batch_inference, chunk, task_input, instruction): chunk
                    for chunk in chunks
                }

                for future in as_completed(future_to_chunk):
                    try:
                        batch_res, batch_usage = future.result()
                        results_map.update(batch_res)

                        # 累加 Usage
                        for k, v in batch_usage.items():
                            usage_acc[k] = usage_acc.get(k, 0) + v

                        # 保存断点 (简化版)
                        save_data = {str(k): v.model_dump() for k, v in results_map.items()}
                        result_path.write_text(json.dumps(save_data, ensure_ascii=False), encoding='utf-8')

                    except Exception as e:
                        self.logger.error(f"Batch failed: {e}")

        # 组装
        final_list = []
        valid_count = 0
        missing_count = 0
        for f in frames:
            vis = results_map.get(f.frame_id)
            if vis:
                valid_count += 1
                if vis.visual_mood_tags:
                    vis.visual_mood_tags = TagManager.normalize_tags(vis.visual_mood_tags, 'visual_mood', True)
            else:
                missing_count += 1
                # [Fix] 容错处理：如果某张图推理失败（不在结果集中），填充空对象，避免 Pydantic 报错
                vis = VisualAnalysisData()

            final_list.append(FrameAnalysisResult(
                frame_id=f.frame_id,
                visual_analysis=vis
            ))

        self.logger.info(f"Visual Analysis Summary: {valid_count} valid, {missing_count} missing/failed.")

        return final_list, usage_acc

    def _batch_inference(self, frames: List[VisualFrameInput], task_input: VisualAnalyzerPayload, instruction: str) -> tuple[
        Dict[str, VisualAnalysisData], Dict]:
        if not self.vertex_client:
            return {}, {}

        model_name = task_input.visual_model        
        valid_frames = frames

        contents: List[Union[str, types.Part]] = [instruction]

        for f in valid_frames:
            # 使用 Frame ID 替代 Slice ID
            contents.append(f"\n--- Frame ID: {f.frame_id} ---")
            try:
                part = self._load_image_part(f.path)
                if part:
                    contents.append(part)
                else:
                    contents.append("[Missing Frame Data]")
            except Exception as e:
                self.logger.warning(f"Skipping frame {f.path}: {e}")
                contents.append("[Error Loading Frame]")

        # [Debug] 记录请求日志
        if getattr(self.gemini_processor, 'debug_mode', False):
            self._save_debug_log("req", contents, model_name)

        # [Fix] 手动预计算多模态输入的 Token，因为 API 返回的 usage_metadata 可能不包含图片 Token
        pre_calculated_input_tokens = 0
        clean_model = model_name.replace("models/", "")

        try:
            # count_tokens 会准确计算文本+图片的 Token 总量
            count_response = self.vertex_client.models.count_tokens(
                model=clean_model,
                contents=contents
            )
            pre_calculated_input_tokens = count_response.total_tokens
            self.logger.info(f"Pre-calculated input tokens for batch: {pre_calculated_input_tokens}")
        except Exception as e:
            # 如果计算失败，仅记录警告，后续仍然尝试使用 API 返回的（可能不准的）用量
            self.logger.warning(f"Could not pre-calculate tokens for cost estimation: {e}")

        try:
            response = self.vertex_client.models.generate_content(
                model=clean_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=BatchVisualOutput,
                )
            )

            # [Debug] 记录响应日志
            if getattr(self.gemini_processor, 'debug_mode', False):
                self._save_debug_log("resp", response.text, model_name)

            if not response.parsed:
                raise ValueError(f"SDK failed to parse response: {response.text}")

            batch_output: BatchVisualOutput = response.parsed
            result_map = {}
            if batch_output and batch_output.results:
                for res_item in batch_output.results:
                    # res_item 是 FrameAnalysisResult
                    if res_item.visual_analysis:
                        result_map[res_item.frame_id] = res_item.visual_analysis

            # [Fix] 使用预计算的 Token 修正成本
            usage_dict = self.gemini_processor.normalize_usage(response)

            # 如果手动计算的输入 Token > API 返回的输入 Token，说明 API 漏算了图片部分
            if pre_calculated_input_tokens > usage_dict.get("prompt_tokens", 0):
                self.logger.info(
                    f"Correcting prompt_tokens from {usage_dict.get('prompt_tokens', 0)} to {pre_calculated_input_tokens}"
                )
                usage_dict["prompt_tokens"] = pre_calculated_input_tokens
                usage_dict["total_tokens"] = pre_calculated_input_tokens + usage_dict.get("completion_tokens", 0)

            return result_map, usage_dict

        except Exception as e:
            self.logger.error(f"Batch inference failed: {e}")
            return {}, {}

    def _load_image_part(self, path_str: str) -> Optional[types.Part]:
        if path_str.startswith("gs://"):
            return types.Part.from_uri(file_uri=path_str, mime_type="image/jpeg")
        else:
            local_path = Path(path_str)
            if not local_path.is_absolute():
                local_path = settings.SHARED_ROOT / local_path
            if local_path.exists():
                return types.Part.from_bytes(data=local_path.read_bytes(), mime_type="image/jpeg")
            else:
                return None

    def _read_file(self, path_str: str) -> str:
        # 复用 GCS/Local 读取逻辑
        if path_str.startswith("gs://"):
            parts = path_str[5:].split("/", 1)
            client = storage.Client(project=self.project_id)
            return client.bucket(parts[0]).blob(parts[1]).download_as_text()

        local_path = Path(path_str)
        if not local_path.is_absolute():
            local_path = settings.SHARED_ROOT / local_path

        return local_path.read_text(encoding='utf-8')

    def _load_frames(self, task_input: VisualAnalyzerPayload) -> List[VisualFrameInput]:
        if task_input.frames:
            return task_input.frames

        if task_input.frames_file_path:
            self.logger.info(f"Loading frames from file: {task_input.frames_file_path}")
            try:
                content = self._read_file(task_input.frames_file_path)
                raw_list = json.loads(content)
                return [VisualFrameInput(**item) for item in raw_list]
            except Exception as e:
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Failed to load frames file: {e}")
        return []

    def _save_debug_log(self, prefix: str, data: Any, model_name: str):
        """辅助方法：手动保存 Debug 日志"""
        try:
            debug_dir = getattr(self.gemini_processor, 'debug_dir', None)
            if not debug_dir:
                return

            timestamp = int(time.time() * 1000)
            filename = f"{prefix}_{timestamp}_{model_name.replace('/', '_')}.json"
            file_path = debug_dir / filename

            # 处理 Request 中包含的二进制图片数据，避免 JSON 序列化失败
            if prefix == "req" and isinstance(data, list):
                serializable_data = []
                for item in data:
                    if isinstance(item, types.Part):
                        # Part 对象没有直接的 mime_type，需要检查子属性
                        mime = "unknown"
                        if hasattr(item, 'inline_data') and item.inline_data:
                            mime = getattr(item.inline_data, 'mime_type', 'unknown')
                        elif hasattr(item, 'file_data') and item.file_data:
                            mime = getattr(item.file_data, 'mime_type', 'unknown')
                        serializable_data.append(f"<Binary/URI Part: {mime}>")
                    else:
                        serializable_data.append(item)
                content = json.dumps(serializable_data, ensure_ascii=False, indent=2)
            else:
                content = str(data)

            file_path.write_text(content, encoding='utf-8')
        except Exception as e:
            self.logger.warning(f"Failed to save debug log: {e}")