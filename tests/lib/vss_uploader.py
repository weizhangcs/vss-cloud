import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# 引入您项目中的基础设施
from file_service.infrastructure.gcs_storage import upload_file_to_gcs


class VSSMediaUploader:
    def __init__(self, bucket_name: str, project_id: str = "vss-dev"):
        self.bucket_name = bucket_name
        self.project_id = project_id

    def upload_slice_assets(self, slices: List[Dict[str, Any]], video_title: str) -> List[Dict[str, Any]]:
        """
        遍历切片数据，将本地 frame.path 上传至 GCS，并替换为 gs:// URI
        """
        print(f">>> [Transfer] Uploading assets for {len(slices)} slices to gs://{self.bucket_name}...")

        # 1. 收集所有唯一的图片路径
        unique_tasks = {}  # local_path -> gcs_object_name
        for s in slices:
            for f in s.get('frames', []):
                local_p = Path(f['path'])
                if local_p.exists():
                    # 构造 GCS 路径: vss_assets/{video_title}/{filename}
                    obj_name = f"vss_assets/{video_title}/{local_p.name}"
                    unique_tasks[str(local_p)] = obj_name

        print(f">>> [Transfer] Found {len(unique_tasks)} unique files to upload.")

        # 2. 并发上传
        uploaded_map = {}  # local_path_str -> gs_uri

        with ThreadPoolExecutor(max_workers=16) as executor:  # 上传是 IO 密集，可以开大点
            future_to_path = {
                executor.submit(self._upload_worker, local_p, obj_name): local_p
                for local_p, obj_name in unique_tasks.items()
            }

            completed = 0
            for future in as_completed(future_to_path):
                try:
                    local_p, gs_uri = future.result()
                    uploaded_map[local_p] = gs_uri
                    completed += 1
                    if completed % 100 == 0:
                        print(f"   Uploading: {completed}/{len(unique_tasks)}...", end='\r')
                except Exception as e:
                    print(f"   ❌ Upload Error: {e}")

        print(f"\n>>> [Transfer] Upload completed.")

        # 3. 替换 JSON 中的路径
        # 深拷贝以避免修改原对象，或者直接修改
        remote_slices = json.loads(json.dumps(slices))
        for s in remote_slices:
            for f in s.get('frames', []):
                local_p = str(f['path'])
                if local_p in uploaded_map:
                    f['path'] = uploaded_map[local_p]  # 关键：替换为 gs://

        return remote_slices

    def _upload_worker(self, local_path_str, gcs_object_name):
        local_path = Path(local_path_str)
        upload_file_to_gcs(local_path, self.bucket_name, gcs_object_name)
        return local_path_str, f"gs://{self.bucket_name}/{gcs_object_name}"