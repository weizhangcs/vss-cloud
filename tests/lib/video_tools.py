import shutil
import subprocess
import sys
from pathlib import Path


def extract_frames_for_slice(video_path: Path, start: float, end: float, slice_id: int, output_dir: Path):
    """
    [Edge Action] 为单个 Slice 提取 Start/Mid/End 三帧
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    mid = start + (end - start) / 2
    points = [("start", start), ("mid", mid), ("end", end)]
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

    for label, timestamp in points:
        filename = f"slice_{slice_id}_{label}.jpg"
        out_path = output_dir / filename
        if not out_path.exists():
            # -q:v 2 保证截图质量较高
            cmd = [
                ffmpeg_bin, "-y", "-ss", str(timestamp), "-i", str(video_path),
                "-frames:v", "1", "-q:v", "2", str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if out_path.exists():
            frames.append({
                "timestamp": timestamp,
                "path": str(out_path.resolve()),
                "position": label
            })
    return frames


def cut_scenes_from_video(video_path: Path, scenes: list, annotated_slices: list, output_dir: Path):
    """
    [Validation Tool] 根据 AI 推理出的场景列表，物理切割视频以便人工验收
    """
    if not scenes or not annotated_slices:
        print("⚠️ No scenes or slice data to cut.")
        return

    print(f"\n✂️  Starting Physical Cutting for {len(scenes)} Scenes...")
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

    # 建立查找表: Slice ID -> Slice Data
    slice_map = {s['slice_id'] if isinstance(s, dict) else s.slice_id: s for s in annotated_slices}

    for sc in scenes:
        idx = sc.get('index', 0) if isinstance(sc, dict) else sc.index
        start_id = sc.get('start_slice_id') if isinstance(sc, dict) else sc.start_slice_id
        end_id = sc.get('end_slice_id') if isinstance(sc, dict) else sc.end_slice_id

        start_slice = slice_map.get(start_id)
        end_slice = slice_map.get(end_id)

        if not start_slice or not end_slice:
            print(f"  [Skip] Scene {idx}: Missing slice boundaries.")
            continue

        start_time = start_slice.get('start_time') if isinstance(start_slice, dict) else start_slice.start_time
        end_time = end_slice.get('end_time') if isinstance(end_slice, dict) else end_slice.end_time

        filename = f"scene_{idx:03d}_{start_time:.1f}s_to_{end_time:.1f}s.mp4"
        out_path = output_dir / filename

        # 使用重编码模式保证画面精准
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(video_path),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "copy",
            str(out_path)
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            print(f"  [OK] Saved: {filename}")
        except subprocess.CalledProcessError:
            print(f"  [Fail] Failed to cut scene {idx}")

    print(f"📂 All clips saved to: {output_dir}")