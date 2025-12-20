# ai_services/biz_services/scene_pre_annotator/service.py

import logging
from pathlib import Path
from typing import Dict, Any, List
from PIL import Image

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats

from core.exceptions import BizException
from core.error_codes import ErrorCode

from .schemas import (
    ScenePreAnnotatorPayload, ScenePreAnnotatorResult, AnnotatedSliceResult,
    VisualAnalysisOutput, SemanticAnalysisOutput, SliceInput
)

logger = logging.getLogger(__name__)


class ScenePreAnnotatorService(AIServiceMixin):
    """
    [Service] 场景预标注服务 (Scene Pre-Annotator).
    职责：接收 Edge 切好的 Slice (含本地图片路径/文本)，进行多模态推理。
    """

    SERVICE_NAME = "scene_pre_annotator"

    # [Config]
    DEFAULT_TEMP = 0.1

    def __init__(self,
                 logger: logging.Logger,
                 gemini_processor: GeminiProcessor,
                 cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Scene Pre-Annotation...")

        # 1. 校验与解析
        try:
            task_input = ScenePreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        total_usage_accumulator = {}
        results: List[AnnotatedSliceResult] = []

        # 显式获取配置
        temperature = payload.get('temperature', self.DEFAULT_TEMP)

        # 2. 遍历推理 (Serial Processing for PoC, can be async later)
        total_slices = len(task_input.slices)

        for idx, slice_item in enumerate(task_input.slices):
            self.logger.info(f"Processing Slice {idx + 1}/{total_slices} ({slice_item.type})...")

            annotated_item = AnnotatedSliceResult(
                slice_id=slice_item.slice_id,
                start_time=slice_item.start_time,
                end_time=slice_item.end_time,
                type=slice_item.type
            )

            try:
                if slice_item.type == "visual_segment":
                    # --- Vision Branch ---
                    if not slice_item.frames:
                        self.logger.warning(f"Visual slice {slice_item.slice_id} has no frames. Skipping.")
                        continue

                    # 加载本地图片
                    pil_images = []
                    for frame_ref in slice_item.frames:
                        path = Path(frame_ref.path)
                        if path.exists():
                            pil_images.append(Image.open(path))
                        else:
                            self.logger.warning(f"Frame not found: {path}")

                    if pil_images:
                        prompt_tpl = self._load_prompt_template(
                            self.prompts_dir, task_input.lang, "visual_inference"
                        )
                        # 组装 Multimodal Payload: [Text, Img1, Img2, Img3]
                        contents = [prompt_tpl.format(video_title=task_input.video_title)] + pil_images

                        resp, usage = self.gemini_processor.generate_content(
                            model_name=task_input.visual_model,
                            prompt=contents,
                            response_schema=VisualAnalysisOutput,
                            temperature=temperature
                        )
                        self._aggregate_usage(total_usage_accumulator, usage)
                        annotated_item.visual_analysis = resp

                elif slice_item.type == "dialogue":
                    # --- Text Branch ---
                    prompt = self._build_prompt(
                        prompts_dir=self.prompts_dir,
                        prompt_name="semantic_inference",
                        lang=task_input.lang,
                        video_title=task_input.video_title,
                        text_content=slice_item.text_content or ""
                    )

                    resp, usage = self.gemini_processor.generate_content(
                        model_name=task_input.text_model,
                        prompt=prompt,
                        response_schema=SemanticAnalysisOutput,
                        temperature=temperature
                    )
                    self._aggregate_usage(total_usage_accumulator, usage)
                    annotated_item.semantic_analysis = resp

            except Exception as e:
                self.logger.error(f"Inference failed for slice {slice_item.slice_id}: {e}")
                annotated_item.reasoning = f"Error: {str(e)}"

            results.append(annotated_item)

        # 3. 汇总报告
        final_stats_obj = UsageStats(model_used="mixed", **total_usage_accumulator)
        cost_report = self.cost_calculator.calculate(final_stats_obj)

        result = ScenePreAnnotatorResult(
            annotated_slices=results,
            stats={
                "total_input_slices": total_slices,
                "processed_count": len(results)
            },
            usage_report=cost_report.to_dict()
        )

        return result.model_dump()