# utils/gcs_utils.py

import datetime
import logging
from pathlib import Path

from google.cloud import storage
from django.conf import settings

# 获取一个标准的日志记录器
logger = logging.getLogger(__name__)


def download_blob_to_file(gcs_path: str, local_destination_path: Path):
    """
    从GCS下载一个文件(blob)到本地指定路径。

    Args:
        gcs_path (str): 文件的完整GCS路径 (例如, "gs://bucket-name/path/to/file.json").
        local_destination_path (Path): 本地保存路径 (一个Path对象).
    """
    try:
        storage_client = storage.Client()
        blob = storage.Blob.from_string(gcs_path, client=storage_client)

        # 确保目标目录存在
        local_destination_path.parent.mkdir(parents=True, exist_ok=True)

        blob.download_to_filename(local_destination_path)
        logger.info(f"Successfully downloaded GCS file '{gcs_path}' to '{local_destination_path}'.")
    except Exception as e:
        logger.error(f"Failed to download GCS file '{gcs_path}': {e}", exc_info=True)
        raise


def upload_file_to_gcs(local_file_path: Path, bucket_name: str, gcs_object_name: str):
    """
    [新增] 将本地的单个文件上传到GCS指定的存储桶和对象名。

    Args:
        local_file_path (Path): 本地源文件的路径。
        bucket_name (str): 目标GCS存储桶名称。
        gcs_object_name (str): GCS中的目标对象完整名称 (例如, "results/project-x/output.json").
    """
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
    """
    将本地一个目录下的所有文件，上传到GCS指定的存储桶和前缀下。

    Args:
        local_dir (Path): 本地源目录。
        bucket_name (str): 目标GCS存储桶名称。
        gcs_prefix (str): GCS中的目标路径前缀 (例如, "results/project-x/").
    """
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
    """
    为一个即将上传的文件，生成一个有时效性的“预签名URL”。

    Args:
        bucket_name (str): 目标GCS存储桶名称。
        object_name (str): 上传后，文件在GCS中的完整路径 (例如, "uploads/project-x/blueprint.json").
        expiration_seconds (int): URL的有效时长（秒）。默认为1小时。

    Returns:
        str: 一个可用于HTTP PUT请求的URL。
    """
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