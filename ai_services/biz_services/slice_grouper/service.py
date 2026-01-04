import logging
from pathlib import Path
from typing import Dict, Any, List

from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.ai_platform.llm.schemas import UsageStats
from core.exceptions import BizException
from core.error_codes import ErrorCode

from ai_services.biz_services.scene_pre_annotator.schemas import (
    ScenePreAnnotatorPayload, AnnotatedSliceResult, SceneSegmentationResponse
)
from ai_services.biz_services.scene_pre_annotator.i18n import get_localized_term

logger = logging.getLogger(__name__)

class SliceGrouperService(AIServiceMixin):
    SERVICE_NAME = "slice_grouper"
    BATCH_SIZE = 250

    def __init__(self, logger: logging.Logger, gemini_processor: GeminiProcessor, cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        # 暂时复用 Prompt，建议后续迁移到当前包下
        self.prompts_dir = Path(__file__).parent.parent / "scene_pre_annotator" / "prompts"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Slice Grouper...")

        try:
            task_input = ScenePreAnnotatorPayload(**payload)
        except Exception as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Schema Error: {e}")

        # 必须提供已标注的切片
        if not task_input.injected_annotated_slices:
             raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, "Slice Grouper requires 'injected_annotated_slices'.")
        
        annotated_slices = task_input.injected_annotated_slices
        self.logger.info(f"Grouping {len(annotated_slices)} annotated slices.")

        scenes, usage_acc = self._group_slices(annotated_slices, task_input)

        cost_report = self.cost_calculator.calculate(UsageStats(model_used=task_input.text_model, **usage_acc))

        return {
            "scenes": [s.model_dump() for s in scenes],
            "stats": {"input_slices": len(annotated_slices), "output_scenes": len(scenes)},
            "usage_report": cost_report.to_dict()
        }

    def _group_slices(self, slices: List[AnnotatedSliceResult], task_input: Any):
        all_scenes = []
        usage_accumulator = {}
        global_idx = 1

        chunks = [slices[i:i + self.BATCH_SIZE] for i in range(0, len(slices), self.BATCH_SIZE)]

        for i, chunk in enumerate(chunks):
            # 1. 构建 Log
            log_text = self._build_slice_log(chunk, task_input.lang)
            
            # 2. 构建 Prompt
            prompt = self._build_prompt(task_input, log_text)

            # 3. LLM 推理
            try:
                resp, usage = self.gemini_processor.generate_content(
                    model_name=task_input.text_model,
                    prompt=prompt,
                    response_schema=SceneSegmentationResponse,
                    temperature=0.1
                )
                
                for k, v in usage.items():
                    usage_accumulator[k] = usage_accumulator.get(k, 0) + v

                if resp and resp.scenes:
                    for scene in resp.scenes:
                        scene.index = global_idx
                        global_idx += 1
                        all_scenes.append(scene)
            except Exception as e:
                self.logger.error(f"Grouping failed for chunk {i}: {e}")

        return all_scenes, usage_accumulator

    def _build_slice_log(self, slices: List[AnnotatedSliceResult], lang: str) -> str:
        lines = []
        for s in slices:
            vis_part = ""
            if s.visual_analysis:
                v = s.visual_analysis
                shot = get_localized_term(v.shot_type, lang)
                tags = ", ".join(v.visual_mood_tags) if v.visual_mood_tags else ""
                vis_part = f"📷 {shot} | {v.subject} | {v.action} | [{tags}]"
            
            lines.append(f"[ID:{s.slice_id}] ({s.start_time:.1f}-{s.end_time:.1f}) {s.text_content[:50]} {vis_part}")
        return "\n".join(lines)

    def _build_prompt(self, task_input, log_text):
        # 简化的 Prompt 读取逻辑
        p_file = self.prompts_dir / task_input.lang / "scene_segmentation.txt"
        if not p_file.exists(): p_file = self.prompts_dir / "en" / "scene_segmentation.txt"
        return p_file.read_text(encoding='utf-8').format(slice_log=log_text, **task_input.model_dump())