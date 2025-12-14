# file_service/infrastructure/gcs_storage.py

import datetime
import logging
from pathlib import Path

from google.cloud import storage
# from django.conf import settings # 如果代码中没用到 settings 可以移除，原代码似乎只在 generate_upload_presigned_url 中未直接使用 settings，但在 upload_file_to_gcs 的参数中由调用者传入 bucket_name

# 获取当前模块的 Logger
logger = logging.getLogger(__name__)

def download_blob_to_file(gcs_path: str, local_destination_path: Path):
    """从GCS下载一个文件(blob)到本地指定路径。"""
    try:
        storage_client = storage.Client()
        blob = storage.Blob.from_string(gcs_path, client=storage_client)
        local_destination_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(local_destination_path)
        logger.info(f"Successfully downloaded GCS file '{gcs_path}' to '{local_destination_path}'.")
    except Exception as e:
        logger.error(f"Failed to download GCS file '{gcs_path}': {e}", exc_info=True)
        raise

def upload_file_to_gcs(local_file_path: Path, bucket_name: str, gcs_object_name: str):
    """将本地的单个文件上传到GCS指定的存储桶和对象名。"""
    if not local_file_path.is_file():
        raise FileNotFoundError(f"Source file not found at: {local_file_path}")

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_object_name)
        blob.upload_from_filename(str(local_file_path))
        logger.info(f"Successfully uploaded file '{local_file_path}' to 'gs://{bucket_name}/{gcs_object_name}'.")
    except Exception as e:
        logger.error(f"Failed to upload file to GCS: {e}", exc_info=True)
        raise

def upload_directory_to_gcs(local_dir: Path, bucket_name: str, gcs_prefix: str):
    """将本地一个目录下的所有文件，上传到GCS指定的存储桶和前缀下。"""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        for local_file in local_dir.glob('**/*'):
            if local_file.is_file():
                relative_path = local_file.relative_to(local_dir)
                blob = bucket.blob(f"{gcs_prefix}/{relative_path}")
                blob.upload_from_filename(str(local_file))
        logger.info(f"Successfully uploaded directory '{local_dir}' to 'gs://{bucket_name}/{gcs_prefix}'.")
    except Exception as e:
        logger.error(f"Failed to upload directory to GCS: {e}", exc_info=True)
        raise

def generate_upload_presigned_url(bucket_name: str, object_name: str, expiration_seconds: int = 3600) -> str:
    """生成预签名上传URL。"""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        url = blob.generate_signed_url(
            version="v4",
            method="PUT",
            expiration=datetime.timedelta(seconds=expiration_seconds),
            headers={"Content-Type": "application/octet-stream"},
        )
        logger.info(f"Successfully generated a presigned URL for uploading to '{object_name}'.")
        return url
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}", exc_info=True)
        raise