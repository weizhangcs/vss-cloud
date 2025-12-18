#ai_services/ai_core_units/narration/utils.py
import re

def sanitize_text(text: str) -> str:
    """[原子能力] 清洗文本，移除舞台指示。"""
    if not text:
        return ""
    return re.sub(r'（.*?）|\(.*?\)', '', text).strip()