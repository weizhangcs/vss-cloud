# task_manager/handlers/scene_pre_annotator.py

import json
from pathlib import Path
from typing import List, Dict, Any

from django.conf import settings
from google.cloud import storage

from task_manager.models import Task
from task_manager.handlers.base import BaseTaskHandler
from task_manager.handlers.registry import HandlerRegistry

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.scene_pre_annotator.service import ScenePreAnnotatorService
from ai_services.biz_services.scene_pre_annotator.schemas import ScenePreAnnotatorPayload, SliceInput

from core.exceptions import BizException
from core.error_codes import ErrorCode


@HandlerRegistry.register(Task.TaskType.SCENE_PRE_ANNOTATOR)
class ScenePreAnnotatorHandler(BaseTaskHandler):
    """
    [Handler] 场景预标注任务 (V3.8 Refactored)
    职责升级：
    1. Schema 校验。
    2. [新增] 引用数据加载 (Pass-by-Reference Loading)。
    3. 资源物理检查 (Fail Fast)。
    4. 调用 Service。
    """

    def handle(self, task: Task) -> dict:
        self.logger.info(f"🚀 Starting SCENE_PRE_ANNOTATOR Task: {task.id}")

        # 1. 基础设施
        debug_dir = settings.SHARED_LOG_ROOT / f"scene_annotator_{task.id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=self.logger,
            debug_mode=True,
            debug_dir=debug_dir
        )

        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )

        service = ScenePreAnnotatorService(
            logger=self.logger,
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator
        )

        # 2. Payload 处理
        try:
            # 2.1 基础 Schema 校验
            payload_obj = ScenePreAnnotatorPayload(**task.payload)

            # 2.2 [架构升级] 数据解包 (Hydration)
            # 如果是引用模式 (slices_file_path)，在此处加载为实体数据
            if not payload_obj.slices and payload_obj.slices_file_path:
                self.logger.info(f"Loading large payload from: {payload_obj.slices_file_path}")
                raw_slices_data = self._load_external_slices(payload_obj.slices_file_path)
                # 转换为 Pydantic 对象列表
                payload_obj.slices = [SliceInput(**item) for item in raw_slices_data]

            # 此时 payload_obj.slices 必定有值 (除非文件为空)
            slice_count = len(payload_obj.slices) if payload_obj.slices else 0
            self.logger.info(f"Payload Ready. Video: {payload_obj.video_title}, Slices: {slice_count}")

            # 2.3 路径物理检查 (Fail Fast)
            if slice_count > 0:
                self._check_local_frames(payload_obj.slices)

            # 2.4 准备 Service 数据
            # 将填充好的完整数据 dump 出来传给 Service
            # 这样 Service 就不需要关心文件读取了，直接处理 slices 列表
            service_payload = payload_obj.model_dump()

        except Exception as e:
            if isinstance(e, BizException): raise e
            # 捕获所有加载/解析异常
            self.logger.error(f"Payload preparation failed: {e}", exc_info=True)
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid Payload or File Error: {e}")

        # 3. 执行
        try:
            result_data = service.execute(service_payload)
        except Exception as e:
            self.logger.error(f"ScenePreAnnotatorService execution failed: {e}", exc_info=True)
            raise e

        # 4. 结果落盘
        output_filename = f"scene_annotation_result_{task.id}.json"
        output_dir = settings.SHARED_TMP_ROOT / f"scene_annotator_{task.id}_workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_filename

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        try:
            rel_output_path = output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            rel_output_path = output_path.name

        self.logger.info(f"✅ Task Finished. Result saved to: {rel_output_path}")

        return {
            "message": "Scene Pre-Annotation completed.",
            "output_file_path": str(rel_output_path),
            "stats": result_data.get("stats"),
            "cost_usage": result_data.get("usage_report")
        }

    def _load_external_slices(self, path_str: str) -> List[Dict[str, Any]]:
        """
        [Helper] 加载外部切片文件 (JSON List)
        支持 gs:// 和 相对路径
        """
        content = ""
        if path_str.startswith("gs://"):
            # GCS 读取
            try:
                # gs://bucket/path/to/file.json
                parts = path_str[5:].split("/", 1)
                bucket_name = parts[0]
                blob_name = parts[1]

                # 使用 settings 中的 Project ID
                project_id = getattr(settings, 'GOOGLE_CLOUD_PROJECT', None)
                client = storage.Client(project=project_id)
                bucket = client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                content = blob.download_as_text(encoding='utf-8')
            except Exception as e:
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Failed to read GCS slices file: {e}")
        else:
            # 本地相对路径读取
            # 锚定到 SHARED_ROOT
            abs_path = settings.SHARED_ROOT / path_str
            if not abs_path.exists():
                raise BizException(ErrorCode.FILE_IO_ERROR, f"Slices file not found: {abs_path}")
            content = abs_path.read_text(encoding='utf-8')

        try:
            data = json.loads(content)
            if not isinstance(data, list):
                # 兼容 { "slices": [...] } 格式
                if isinstance(data, dict) and "slices" in data:
                    return data["slices"]
                raise ValueError("JSON content must be a list of slices.")
            return data
        except json.JSONDecodeError as e:
            raise BizException(ErrorCode.PAYLOAD_VALIDATION_ERROR, f"Invalid JSON in slices file: {e}")

    def _check_local_frames(self, slices: List[SliceInput]):
        """
        [Helper] 检查本地帧文件是否存在
        """
        local_frame_count = 0
        missing_frames = []

        for s in slices:
            for f in s.frames:
                if not f.path.startswith("gs://"):
                    # 锚定到 SHARED_ROOT 进行检查
                    abs_path = settings.SHARED_ROOT / f.path
                    if not abs_path.exists():
                        missing_frames.append(f.path)
                    else:
                        local_frame_count += 1

        if missing_frames:
            error_msg = f"Missing {len(missing_frames)} local frames. Examples: {missing_frames[:3]}"
            raise BizException(ErrorCode.FILE_IO_ERROR, error_msg)

        if local_frame_count > 0:
            self.logger.info(f"Verified {local_frame_count} local frames.")