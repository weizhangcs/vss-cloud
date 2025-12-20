# tests/ai_services/scene_pre_annotator/test_scene_pre_annotator.py

import sys
import json
import shutil
import subprocess
from pathlib import Path

# 环境引导
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]
sys.path.append(str(project_root))

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.scene_pre_annotator.service import ScenePreAnnotatorService
from tests.lib.bootstrap import bootstrap_local_env_and_logger

# 引入之前的 PoC Slicer (假设文件在 tests/core_slicer_test.py)
# 如果 import 失败，请确保 tests/__init__.py 存在
try:
    from tests.core_slicer_test import VSSVideoSlicer
except ImportError:
    print("❌ 无法导入 VSSVideoSlicer，请确保 tests/core_slicer_test.py 存在")
    sys.exit(1)


def extract_frames_for_slice(video_path: Path, start: float, end: float, slice_id: int, output_dir: Path):
    """[模拟 Edge 端] 提取 Start/Mid/End 三帧"""
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []

    duration = end - start
    mid = start + (duration / 2)

    # 定义采样点
    # 注意：FFmpeg -ss 放在 -i 前面更快
    points = [("start", start), ("mid", mid), ("end", end)]

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

    for label, timestamp in points:
        filename = f"slice_{slice_id}_{label}.jpg"
        out_path = output_dir / filename

        if not out_path.exists():
            cmd = [
                ffmpeg_bin, "-y",
                "-ss", str(timestamp),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",  # 质量控制
                str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if out_path.exists():
            frames.append({
                "timestamp": timestamp,
                "path": str(out_path.resolve()),  # 必须转为绝对路径
                "position": label
            })

    return frames


def run_test():
    # 1. 配置输入 (请修改这里指向您的真实测试文件)
    # ==========================================
    VIDEO_FILE = project_root / "shared_media/tmp/scene_pre_annotator/ep01.mp4"

    SUBTITLE_FILE = project_root / "shared_media/tmp/scene_pre_annotator/ep01.srt"
    # ==========================================

    settings, logger = bootstrap_local_env_and_logger(project_root)

    if not VIDEO_FILE.exists() or not SUBTITLE_FILE.exists():
        print(f"⚠️  测试文件不存在: \nVideo: {VIDEO_FILE}\nSub: {SUBTITLE_FILE}")
        print("请在 shared_media/tmp/scene_pre_annotator 下放置测试文件后重试。")
        return

    work_dir = project_root / "shared_media/tmp/scene_pre_annotator"
    frames_dir = work_dir / "frames"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2. [Edge Phase 1] 运行切片器
    print(">>> [Edge] Running Slicer...")
    slicer = VSSVideoSlicer(str(VIDEO_FILE), str(SUBTITLE_FILE))
    raw_result = slicer.process()

    # 3. [Edge Phase 2] 提取图片并组装 Payload
    print(">>> [Edge] Extracting Frames & Building Payload...")
    processed_slices = []

    for item in raw_result['slices']:
        # 复制基本信息
        new_item = {
            "slice_id": len(processed_slices) + 1,  # 生成 ID
            "start_time": item['start_time'],
            "end_time": item['end_time'],
            "type": item['type'],
            "text_content": item.get('text_content')
        }

        # 视觉切片 -> 提取图片
        if item['type'] == 'visual_segment':
            frame_data = extract_frames_for_slice(
                VIDEO_FILE, item['start_time'], item['end_time'], new_item['slice_id'], frames_dir
            )
            new_item['frames'] = frame_data

        processed_slices.append(new_item)

    payload = {
        "video_title": VIDEO_FILE.stem,
        "slices": processed_slices,
        "visual_model": "gemini-2.5-flash",
        "text_model": "gemini-2.5-flash",
        "lang": "zh"
    }

    # 4. [Cloud Phase] 初始化服务
    print(">>> [Cloud] Init Service...")
    processor = GeminiProcessor(settings.GOOGLE_API_KEY, logger, debug_mode=True)
    calculator = CostCalculator(settings.GEMINI_PRICING, settings.USD_TO_RMB_EXCHANGE_RATE)
    service = ScenePreAnnotatorService(logger, processor, calculator)

    # 5. 执行推理
    print(f">>> [Cloud] Executing Inference on {len(processed_slices)} slices...")
    try:
        result = service.execute(payload)

        print("\n" + "=" * 40)
        print("✅ 测试成功! 结果摘要:")
        print("=" * 40)

        annotated = result.get('annotated_slices', [])
        for item in annotated:
            print(f"\n[Slice {item['slice_id']}] {item['type']} ({item['start_time']}s - {item['end_time']}s)")

            if item.get('visual_analysis'):
                vis = item['visual_analysis']
                print(f"  📷 Visual: {vis['subject']} | {vis['action']} | {vis['mood']}")
                print(f"     Shot: {vis['shot_type']} | New Scene? {vis['is_new_scene']}")

            if item.get('semantic_analysis'):
                sem = item['semantic_analysis']
                print(f"  📝 Text: {sem['summary']}")
                print(f"     Func: {sem['narrative_function']} | New Scene? {sem['potential_scene_change']}")

        usage = result.get("usage_report", {})
        print(f"\n💰 Cost: ${usage.get('cost_usd', 0):.4f}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test()