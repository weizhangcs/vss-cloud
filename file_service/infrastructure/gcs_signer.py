# file_service/infrastructure/gcs_signer.py

import logging
import datetime
from typing import List, Dict, Optional
from google.cloud import storage
from django.conf import settings

logger = logging.getLogger(__name__)


class GCSSignerService:
    """
    GCS 签名服务 (V3.7 Control Plane Infrastructure)
    职责: 生成批量预签名上传链接 (Presigned URLs), 实现无密钥文件传输。
    """

    def __init__(self, project_id: Optional[str] = None):
        """
        初始化签名服务。
        优先使用传入的 project_id，否则回退到 Django settings 配置。
        """
        self.project_id = project_id or getattr(settings, 'GOOGLE_CLOUD_PROJECT', None)
        try:
            # 注意: 这里假设运行环境已通过 Service Account (gcp-credentials.json) 鉴权
            # 如果是本地开发，请确保 GOOGLE_APPLICATION_CREDENTIALS 环境变量已设置
            self.client = storage.Client(project=self.project_id)
        except Exception as e:
            logger.error(f"Failed to initialize Google Storage Client: {e}")
            raise

    def generate_batch_upload_urls(self,
                                   bucket_name: str,
                                   file_names: List[str],
                                   prefix: str = "",
                                   expiration_seconds: int = 3600,
                                   content_type: str = "application/octet-stream") -> Dict[str, str]:
        """
        批量生成 PUT 上传的预签名 URL。

        Args:
            bucket_name (str): 目标 GCS 存储桶名称。
            file_names (List[str]): 文件名列表 (仅文件名，不含路径)。
            prefix (str): GCS 存储前缀 (e.g. "assets/uuid/raw/"), 建议以 '/' 结尾。
            expiration_seconds (int): 链接有效期 (秒)，默认 1 小时。
            content_type (str): 预期的 Content-Type Header (客户端上传时必须严格匹配)。

        Returns:
            Dict[str, str]: { "filename.jpg": "https://storage.googleapis.com/..." }
        """
        if not bucket_name:
            raise ValueError("Bucket name is required for generating signed URLs.")

        if not file_names:
            logger.warning("generate_batch_upload_urls called with empty file_names.")
            return {}

        try:
            bucket = self.client.bucket(bucket_name)
            signed_urls = {}

            # 规范化前缀: 确保以 / 结尾 (如果不为空)
            clean_prefix = prefix
            if clean_prefix and not clean_prefix.endswith("/"):
                clean_prefix += "/"

            # 设定统一的过期时间对象
            expiration = datetime.timedelta(seconds=expiration_seconds)

            for name in file_names:
                # 拼接完整对象键 (Key)
                blob_name = f"{clean_prefix}{name}"
                blob = bucket.blob(blob_name)

                # 生成 V4 签名
                # method="PUT": 允许客户端执行上传操作
                url = blob.generate_signed_url(
                    version="v4",
                    method="PUT",
                    expiration=expiration,
                    content_type=content_type,
                )
                signed_urls[name] = url

            logger.info(
                f"✅ Generated {len(signed_urls)} presigned URLs. Bucket: '{bucket_name}', Prefix: '{clean_prefix}'")
            return signed_urls

        except Exception as e:
            logger.error(f"❌ Failed to generate batch signed URLs: {e}", exc_info=True)
            raise