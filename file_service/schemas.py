from pydantic import BaseModel, Field
from typing import Optional

class FileUploadResponse(BaseModel):
    """
    文件上传的标准响应契约。
    """
    relative_path: str = Field(..., description="文件相对于共享根目录的路径")
    # 预留字段，为未来可能返回完整 CDN URL 做准备
    full_url: Optional[str] = Field(None, description="文件的完整访问 URL (可选)")