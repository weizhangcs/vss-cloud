from pydantic import BaseModel
from typing import List

class BrollSelectionResponse(BaseModel):
    """B-Roll 选择服务的输出契约"""
    # LLM 必须返回 ID 列表，如 ["ID-1", "ID-5"]
    selected_ids: List[str]