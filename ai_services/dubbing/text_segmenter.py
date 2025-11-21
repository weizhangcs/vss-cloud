# ai_services/dubbing/text_segmenter.py

import re
import logging
from typing import List


class MultilingualTextSegmenter:
    """
    多语种文本切分器。
    负责将长文本切分为符合 TTS 模型输入限制的短句列表。
    """

    # 中文标点正则: 仅捕获标点本身 (因为中文通常不需要标点后空格)
    ZH_SPLIT_PATTERN = r'([。！？；;!?])'

    # 英文标点正则 (升级版):
    # 1. (?<!Mr)(?<!Dr)... : 负向回顾，忽略常见缩写 (Mr., Dr., Ms., Mrs., St.)
    # 2. [.?!;] : 匹配标点
    # 3. (?:\s+|$) : 匹配后续的空格或行尾 (非捕获组，但包含在整个外层捕获组中)
    # 外层 () 确保 split 后保留这一整块 (标点+空格)
    EN_SPLIT_PATTERN = r'((?:(?<!Mr)(?<!Dr)(?<!Ms)(?<!St)(?<!Mrs))[.?!;](?:\s+|$))'

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def segment(self, text: str, lang: str = "zh", max_len: int = 300) -> List[str]:
        """
        主入口：根据语言和长度限制切分文本。
        """
        if not text:
            return []

        text = text.strip()

        # 2. 初步切分 (按标点打散)
        if lang == "zh":
            raw_segments = self._split_by_pattern(text, self.ZH_SPLIT_PATTERN)
        else:
            raw_segments = self._split_by_pattern(text, self.EN_SPLIT_PATTERN)

        # 3. 贪婪合并 (Merge)
        merged_segments = []
        current_buffer = ""

        for seg in raw_segments:
            # 如果 buffer + seg 没超限，就合并
            if len(current_buffer) + len(seg) <= max_len:
                current_buffer += seg
            else:
                if current_buffer:
                    merged_segments.append(current_buffer)

                # 检查新的 seg 是否独自就超限了 (无标点长文)
                if len(seg) > max_len:
                    # 强制硬切分
                    chunks = [seg[i:i + max_len] for i in range(0, len(seg), max_len)]
                    merged_segments.extend(chunks[:-1])
                    current_buffer = chunks[-1]
                else:
                    current_buffer = seg

        if current_buffer:
            merged_segments.append(current_buffer)

        # 4. 最终清理 (去除首尾空白，但保留句中空格)
        return [s.strip() for s in merged_segments if s.strip()]

    def _split_by_pattern(self, text: str, pattern: str) -> List[str]:
        """
        基于正则切分，并保留标点符号(及可能的空格)在上一句末尾。
        """
        parts = re.split(pattern, text)

        segments = []
        # re.split 行为：[text, delimiter, text, delimiter, ...]
        for i in range(0, len(parts) - 1, 2):
            phrase = parts[i]
            separator = parts[i + 1]  # 这里现在的 separator 包含了标点和空格
            segments.append(phrase + separator)

        if parts[-1]:
            segments.append(parts[-1])

        return segments