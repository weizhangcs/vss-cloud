import sys
import json
import shutil
import subprocess
import time
import logging
import os
from datetime import datetime
from pathlib import Path

# 引入解耦后的模块
from tests.lib.vss_edge_simulator import EdgeSimulator
from tests.lib.vss_uploader import VSSMediaUploader
from tests.lib.video_tools import cut_scenes_from_video

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.scene_pre_annotator.service import ScenePreAnnotatorService
from tests.lib.bootstrap import bootstrap_local_env_and_logger

# ==========================================
# 1. 环境引导 (Bootstrap)
# ==========================================
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]
sys.path.append(str(project_root))

# Django 配置引导 (为了获取 GCS Bucket 等配置)
from django.conf import settings

if not settings.configured:
    # 请替换为您实际的 Bucket Name，或确保环境变量中有 GCS_BUCKET_NAME
    bucket_name = os.getenv("GCS_BUCKET_NAME", "vss_cloud_localhost_dev")
    settings.configure(
        GCS_BUCKET_NAME=bucket_name,
        LOGGING_CONFIG=None  # 避免冲突
    )

if not os.getenv("GOOGLE_CLOUD_PROJECT"):
    # 强制设置一个，Vertex SDK 需要它
    os.environ["GOOGLE_CLOUD_PROJECT"] = "storygraph-465918"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

# ==========================================
# 3. 主编排流程 (Orchestration)
# ==========================================
def run_test():
    # --- A. 配置路径 ---
    VIDEO_FILE = project_root / "shared_media/tmp/scene_pre_annotator/film/EP02.mp4"
    ASS_FILE = project_root / "shared_media/tmp/scene_pre_annotator/film/EP02_ai_labeled.ass"
    WORK_DIR = project_root / "shared_media/tmp/scene_pre_annotator/film"

    # 中间产物 (Checkpoints)
    STEP1_JSON = WORK_DIR / "step1_edge_output.json"  # 包含本地图片路径
    STEP2_JSON = WORK_DIR / "step2_cloud_ready.json"  # 包含 gs:// 路径
    FINAL_JSON = WORK_DIR / "step3_final_result.json"  # 最终结果

    # 引导环境
    settings_obj, logger = bootstrap_local_env_and_logger(project_root)

    # 确保 WORK_DIR 存在
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print("🎬 VSS Scene Pre-Annotator Pipeline (Decoupled)")
    print("==================================================")

    # ==========================================
    # Stage 1: VSS Edge (Slice & Extract)
    # ==========================================
    local_slices = []

    if STEP1_JSON.exists():
        print(f"\n✅ [Stage 1: Edge] Checkpoint found: {STEP1_JSON.name}")
        with open(STEP1_JSON, 'r', encoding='utf-8') as f:
            local_slices = json.load(f)
        print(f"   Loaded {len(local_slices)} slices from local cache.")
    else:
        print(f"\n🚀 [Stage 1: Edge] Running Simulator...")
        try:
            edge = EdgeSimulator(VIDEO_FILE, ASS_FILE, WORK_DIR)
            local_slices = edge.run()

            # 保存 Checkpoint
            with open(STEP1_JSON, 'w', encoding='utf-8') as f:
                json.dump(local_slices, f, ensure_ascii=False, indent=2)
            print(f"   Saved {len(local_slices)} slices to {STEP1_JSON.name}")
        except Exception as e:
            print(f"❌ Stage 1 Failed: {e}")
            return

    # ==========================================
    # Stage 2: VSS Transfer (Upload to GCS)
    # ==========================================
    remote_slices = []

    if STEP2_JSON.exists():
        print(f"\n✅ [Stage 2: Transfer] Checkpoint found: {STEP2_JSON.name}")
        with open(STEP2_JSON, 'r', encoding='utf-8') as f:
            remote_slices = json.load(f)
        print(f"   Loaded {len(remote_slices)} remote slices cache.")
    else:
        print(f"\n🚀 [Stage 2: Transfer] Uploading to GCS ({settings.GCS_BUCKET_NAME})...")
        try:
            uploader = VSSMediaUploader(bucket_name=settings.GCS_BUCKET_NAME)
            remote_slices = uploader.upload_slice_assets(local_slices, VIDEO_FILE.stem)

            # 保存 Checkpoint
            with open(STEP2_JSON, 'w', encoding='utf-8') as f:
                json.dump(remote_slices, f, ensure_ascii=False, indent=2)
            print(f"   Upload complete. Saved manifest to {STEP2_JSON.name}")
        except Exception as e:
            print(f"❌ Stage 2 Failed: {e}")
            return

    # ==========================================
    # Stage 3: VSS Cloud (Inference)
    # ==========================================
    print(f"\n🚀 [Stage 3: Cloud] Executing AI Inference...")

    # 构造 Payload
    # 注意：这里我们传入的是 remote_slices (带 gs:// 链接)
    payload = {
        "video_title": VIDEO_FILE.stem,
        "slices": remote_slices,
        "lang": "en",  # 或 "zh"
        "visual_model": "gemini-2.5-flash",
        "text_model": "gemini-2.5-flash",
        "temperature": 0.1,
        # "injected_annotated_slices": ... # 如果要测试缓存注入，可在这里加载 FINAL_JSON
    }

    try:
        processor = GeminiProcessor(settings_obj.GOOGLE_API_KEY, logger, debug_mode=True)
        calculator = CostCalculator(settings_obj.GEMINI_PRICING, settings_obj.USD_TO_RMB_EXCHANGE_RATE)
        service = ScenePreAnnotatorService(logger, processor, calculator)

        t_start = time.time()
        result_dict = service.execute(payload)
        duration = time.time() - t_start
        print(f"   Inference finished in {duration:.2f}s")

        # 保存最终结果
        with open(FINAL_JSON, 'w', encoding='utf-8') as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2)
        print(f"   ✅ Final Result saved to {FINAL_JSON.name}")

    except Exception as e:
        print(f"❌ Stage 3 Failed: {e}")
        # 如果是 Stage 3 失败，不应该影响 Step 1 和 Step 2 的缓存，下次可以直接重试 Stage 3
        return

    # ==========================================
    # 4. 物理切分 (Post-Processing)
    # ==========================================
    if result_dict and result_dict.get('scenes'):
        output_clips_dir = WORK_DIR / f"clips_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 注意：这里需要传入 result_dict['scenes'] 和 result_dict['annotated_slices']
        # 这里的 annotated_slices 已经是包含了 visual_analysis 的完整数据
        cut_scenes_from_video(
            VIDEO_FILE,
            result_dict['scenes'],
            result_dict['annotated_slices'],
            output_clips_dir
        )
    else:
        print("⚠️ No scenes generated, skipping cut.")


if __name__ == "__main__":
    run_test()