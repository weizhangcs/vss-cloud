# file_service/schemas.py

from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class FileUploadResponse(BaseModel):
    """
    文件上传的标准响应契约。
    """
    relative_path: str = Field(..., description="文件相对于共享根目录的路径")
    # 预留字段，为未来可能返回完整 CDN URL 做准备
    full_url: Optional[str] = Field(None, description="文件的完整访问 URL (可选)")

class UploadTicketRequest(BaseModel):
    """
    [Edge -> Cloud] 上传票据申请请求。
    Edge 告知 Cloud 它想上传哪些文件，以及这些文件属于哪个资产/媒资。
    """
    asset_id: str = Field(..., description="资产逻辑ID (Asset UUID, e.g. 剧集ID)")
    media_id: Optional[str] = Field(None, description="物理媒资ID (Media UUID, e.g. 单集视频ID)。若为空，则视为资产级公共文件。")
    filenames: List[str] = Field(..., description="待上传的文件名列表 (e.g. ['frame_001.jpg'])", min_length=1)

class UploadTicketResponse(BaseModel):
    """
    [Cloud -> Edge] 票据响应。
    包含上传目标路径前缀和具体的预签名链接。
    """
    upload_base_path: str = Field(..., description="GCS 存储基路径 (gs://bucket/prefix/)，用于Edge端后续组装Task Payload")
    signed_urls: Dict[str, str] = Field(..., description="文件名到预签名PUT链接的映射字典")