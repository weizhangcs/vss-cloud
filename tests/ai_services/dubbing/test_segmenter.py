# tests/ai_services/dubbing/test_segmenter.py

import sys
import logging
from pathlib import Path

# 路径引导
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from ai_services.dubbing.text_segmenter import MultilingualTextSegmenter

# 配置简单日志
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("SegmenterTest")


def run_tests():
    segmenter = MultilingualTextSegmenter(logger)

    test_cases = [
        {
            "name": "CN_Normal",
            "lang": "zh",
            "max_len": 50,
            "text": "这是一个测试句子。这是第二句，比较短。这是第三句，稍微长一点点，看看会被切分吗？应该不会吧。"
        },
        {
            "name": "CN_Long_No_Punc",
            "lang": "zh",
            "max_len": 10,  # 极端测试：限制很短
            "text": "这是一个非常非常长且完全没有标点符号的句子它应该会被强制切分因为超过了限制"
        },
        {
            "name": "EN_Normal",
            "lang": "en",
            "max_len": 60,
            "text": "Hello world! This is a test for English segmentation. Mr. Smith is here. We should keep sentences together if they are short."
        },
        {
            "name": "CN_Greedy_Merge",
            "lang": "zh",
            "max_len": 20,  # 限制 20 字
            "text": "短句1。短2。短3。这句很长很长超过了限制单独放。"
            # 预期：[短句1。短2。短3。, 这句很长很长超过了限制单独放。]
        }
    ]

    for case in test_cases:
        print(f"\n--- Test Case: {case['name']} (Lang: {case['lang']}, Max: {case['max_len']}) ---")
        print(f"Input: {case['text']}")

        results = segmenter.segment(case['text'], case['lang'], case['max_len'])

        print(f"Output ({len(results)} segments):")
        for i, seg in enumerate(results):
            print(f"  [{i}] ({len(seg)} chars): {seg}")


if __name__ == "__main__":
    run_tests()