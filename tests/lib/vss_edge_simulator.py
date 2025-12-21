import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# [Change] 引用新的核心库和工具库
from tests.lib.core_slicer_ass import VSSASSSlicer
from tests.lib.video_tools import extract_frames_for_slice


class EdgeSimulator:
    def __init__(self, video_path: Path, ass_path: Path, output_dir: Path):
        self.video_path = video_path
        self.ass_path = ass_path
        self.output_dir = output_dir
        self.frames_dir = output_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> List[Dict[str, Any]]:
        print(f">>> [Edge] 1. Slicing video using VSSASSSlicer...")

        # 1. 切片
        slicer = VSSASSSlicer(str(self.video_path), str(self.ass_path))
        raw_result = slicer.process()
        raw_slices = raw_result['slices']

        print(f">>> [Edge] 2. Extracting frames for {len(raw_slices)} slices (Parallel)...")

        processed_slices = []

        # 2. 并发抽帧
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_idx = {
                executor.submit(self._process_single_slice, item, i): i
                for i, item in enumerate(raw_slices)
            }

            completed = 0
            for future in as_completed(future_to_idx):
                try:
                    res = future.result()
                    processed_slices.append(res)
                    completed += 1
                    if completed % 10 == 0:
                        print(f"   Extracting: {completed}/{len(raw_slices)}...", end='\r')
                except Exception as e:
                    print(f"   ❌ Error: {e}")

        # 3. 排序与输出
        processed_slices.sort(key=lambda x: x['slice_id'])

        output_json = self.output_dir / "step1_edge_output.json"  # 统一命名
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(processed_slices, f, ensure_ascii=False, indent=2)

        print(f"\n>>> [Edge] Output saved to {output_json}")
        return processed_slices

    def _process_single_slice(self, item, idx):
        slice_id = idx + 1
        new_item = {
            "slice_id": slice_id,
            "start_time": item['start_time'],
            "end_time": item['end_time'],
            "type": item['type'],
            "text_content": item.get('text_content'),
            # 调用工具库
            "frames": extract_frames_for_slice(
                self.video_path,
                item['start_time'],
                item['end_time'],
                slice_id,
                self.frames_dir
            )
        }
        return new_item