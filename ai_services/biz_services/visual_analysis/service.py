import json
import shutil
import subprocess
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Dict, Any, List

from PIL import Image
from django.conf import settings

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.mixins import AIServiceMixin
from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    VisualAnalysisPayload, VisualAnalysisResult, RawSlice, VisualTag, RefinedSlice
)


class VisualAnalysisService(AIServiceMixin):
    """
    [Service] 视觉分析服务 (Visual Analysis Service)
    职责：
    1. 视觉推理 (Visual Inference): 对 raw_slices 中的 visual_segment 进行截图 + Gemini VLM 分析。
    2. 语义整形 (Semantic Refinement): 对全量切片进行文本语义合并与打标。
    3. 聚合输出: 生成供 Workbench 使用的最终 Timeline。
    """

    def __init__(self,
                 logger,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator

        # 路径配置
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.work_dir = settings.SHARED_TMP_ROOT / "visual_analysis_workspace"
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # 临时帧保存目录
        self.frames_dir = self.work_dir / "frames"
        self.frames_dir.mkdir(exist_ok=True)

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Visual Analysis Service...")

        # 1. 解析 Payload
        try:
            task_input = VisualAnalysisPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        # 2. 准备文件路径 (兼容绝对/相对路径)
        video_full_path = self._resolve_path(task_input.video_path)
        raw_json_full_path = self._resolve_path(task_input.raw_slices_path)

        # 3. 加载 Raw Slices
        with open(raw_json_full_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            raw_slices = [RawSlice(**s) for s in raw_data.get("slices", [])]
            total_duration = raw_data.get("total_duration", 0.0)

        total_usage = {}  # 用于累计 Token 消耗

        # =========================================================
        # Stage 1: Visual Inference (针对 Visual Segments)
        # =========================================================
        self.logger.info("Stage 1: Processing Visual Segments...")

        visual_prompt_tpl = self._load_prompt_template(task_input.lang, "visual_tagging")

        processed_slices = []
        for idx, slice_item in enumerate(raw_slices):
            # 必须使用 model_copy，否则修改会影响原始对象引用
            current_slice = slice_item.model_copy()

            if current_slice.processing_strategy == "visual_inference":
                mid_point = (current_slice.start_time + current_slice.end_time) / 2
                frame_path = self._extract_frame(video_full_path, mid_point, idx)

                if frame_path:
                    # 记录缩略图路径 (相对路径，供前端访问)
                    try:
                        rel_thumb = frame_path.relative_to(settings.SHARED_ROOT)
                    except ValueError:
                        rel_thumb = frame_path.name
                    current_slice.thumbnail_path = str(rel_thumb)

                    # 调用 Gemini VLM
                    try:
                        pil_image = Image.open(frame_path)
                        response_data, usage = self.gemini_processor.generate_content(
                            model_name=task_input.visual_model,
                            prompt=[visual_prompt_tpl, pil_image],  # 多模态 List 输入
                            temperature=0.2
                        )

                        # 解析结果
                        visual_tag = VisualTag(**response_data)
                        current_slice.visual_analysis = visual_tag

                        # 计费聚合
                        self._calculate_and_merge_cost(task_input.visual_model, usage, total_usage)

                    except Exception as e:
                        self.logger.error(f"Visual Inference failed for slice {idx}: {e}")

            processed_slices.append(current_slice)

        # =========================================================
        # Stage 2: Semantic Refinement (全量切片)
        # =========================================================
        self.logger.info("Stage 2: Semantic Refinement...")

        # 准备 Prompt 上下文
        # 简化数据结构以节省 Token
        context_slices = []
        for i, s in enumerate(processed_slices):
            item = {
                "id": i,
                "time": f"{s.start_time}-{s.end_time}",
                "type": s.type,
                "content": s.text_content if s.type == "dialogue" else f"[Visual: {s.visual_analysis.action if s.visual_analysis else 'Unknown'}]"
            }
            context_slices.append(item)

        semantic_prompt = self._build_prompt(
            "semantic_refinement",
            lang=task_input.lang,
            slices_json=json.dumps(context_slices, indent=2)
        )

        # 调用 Gemini Logic
        try:
            refine_resp, refine_usage = self.gemini_processor.generate_content(
                model_name=task_input.semantic_model,
                prompt=semantic_prompt,
                temperature=0.1
            )
            self._calculate_and_merge_cost(task_input.semantic_model, refine_usage, total_usage)

            refined_timeline_raw = refine_resp.get("refined_timeline", [])

        except Exception as e:
            self.logger.error(f"Semantic Refinement failed: {e}")
            raise BizException(ErrorCode.LLM_INFERENCE_ERROR, f"Refinement failed: {e}")

        # =========================================================
        # Stage 3: Aggregation (回填数据)
        # =========================================================
        final_timeline = []

        # 建立原始切片查找表
        raw_map = {i: s for i, s in enumerate(processed_slices)}

        for item in refined_timeline_raw:
            # 基础字段
            refined_slice = RefinedSlice(
                start_time=item["start_time"],
                end_time=item["end_time"],
                type=item["type"],
                topic=item.get("topic", "Unknown"),
                content=item["content"],
                source_slice_ids=item.get("source_slice_ids", []),
                refinement_note=f"Merged {len(item.get('source_slice_ids', []))} slices"
            )

            # 如果是 Visual Segment，需要回填刚才 Stage 1 跑出来的视觉结果
            if refined_slice.type == "visual_segment":
                # 策略：找到重叠最大的那个原始 Visual Slice
                best_match = None
                for raw_idx in refined_slice.source_slice_ids:
                    raw_s = raw_map.get(raw_idx)
                    if raw_s and raw_s.type == "visual_segment":
                        best_match = raw_s
                        break  # 通常 Visual Segment 是一对一的

                # 如果 source_ids 为空或没找到 (LLM 可能调整了 ID)，尝试时间匹配兜底
                if not best_match:
                    for raw_s in processed_slices:
                        if raw_s.type == "visual_segment" and abs(raw_s.start_time - refined_slice.start_time) < 0.1:
                            best_match = raw_s
                            break

                if best_match:
                    refined_slice.visual_tags = best_match.visual_analysis
                    refined_slice.thumbnail_path = best_match.thumbnail_path

            final_timeline.append(refined_slice)

        # 3. 构建最终结果
        result = VisualAnalysisResult(
            video_path=task_input.video_path,
            total_duration=total_duration,
            timeline=final_timeline,
            stats={
                "original_slices": len(raw_slices),
                "refined_slices": len(final_timeline)
            },
            usage_report=total_usage
        )

        self.logger.info(f"✅ Service Finished. Total Cost: ${total_usage.get('total_cost_usd', 0):.4f}")
        return result.model_dump()

    def _resolve_path(self, path_str: str) -> Path:
        """解析路径：如果是绝对路径则保持，如果是相对路径则基于 SHARED_ROOT"""
        p = Path(path_str)
        if p.is_absolute():
            return p
        return settings.SHARED_ROOT / p

    def _extract_frame(self, video_path: Path, timestamp: float, idx: int) -> Path:
        """FFmpeg 截图"""
        out_name = f"frame_{video_path.stem}_{timestamp:.2f}_{idx}.jpg"
        out_path = self.frames_dir / out_name

        if out_path.exists():
            return out_path

        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg_bin, "-y", "-ss", str(timestamp),
            "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2",
            str(out_path)
        ]

        try:
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, check=True
            )
            return out_path
        except Exception as e:
            self.logger.warning(f"FFmpeg extraction failed for {video_path}: {e}")
            return None

    def _calculate_and_merge_cost(self, model_name: str, usage: Dict, total_usage: Dict):
        """计算成本并累加到总报表"""
        costs = self.cost_calculator.calculate(model_name, usage)

        # 累加 Token
        total_usage["total_prompt_tokens"] = total_usage.get("total_prompt_tokens", 0) + usage.get("prompt_tokens", 0)
        total_usage["total_completion_tokens"] = total_usage.get("total_completion_tokens", 0) + usage.get(
            "completion_tokens", 0)

        # 累加 Cost
        current_usd = costs.get("cost_usd", 0)
        total_usage["total_cost_usd"] = total_usage.get("total_cost_usd", 0) + current_usd
        total_usage["total_cost_rmb"] = total_usage.get("total_cost_rmb", 0) + costs.get("cost_rmb", 0)