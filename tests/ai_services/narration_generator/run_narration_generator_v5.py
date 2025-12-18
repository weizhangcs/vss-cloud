# tests/ai_services/narration_generator/run_narration_generator_v5.py
import json
import sys
import logging
from pathlib import Path
from typing import List, Any

# 1. 环境路径设置 (确保能找到项目根目录)
# 假设脚本在 tests/ai_services/narration_generator/ 下，向上找 3 层
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ai_services.biz_services.narrative_dataset import NarrativeDataset, NarrativeScene, NarrativeTimeline, TimelineNode
from ai_services.biz_services.narration.schemas import NarrationServiceConfig, ControlParams
from ai_services.biz_services.narration.narration_generator_v5 import NarrationGenerator
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator


def mock_logger():
    logging.basicConfig(level=logging.INFO)
    return logging.getLogger("MockNarration")


# --- MOCK 数据构建 ---

def mock_narrative_dataset() -> NarrativeDataset:
    # 直接使用您提供的真实 JSON 结构
    raw_json = {
        "project_metadata": {
            "project_name": "总裁的契约女友_v3",
            "version": "2.1"
        },
        "scenes": {
            "1": {
                "id": 1,
                "chapter_id": 1,
                "start_time": "00:00:00.000",
                "end_time": "00:00:23.096",
                "inferred_location": "在咖啡店里",
                "character_dynamics": "车小小浓妆打扮后来到约定好的相亲地点...",
                "mood_and_atmosphere": "Calm",
                "dialogues": [
                    {"content": "车小小，好戏开场了", "speaker": "车小小", "start_time": "00:00:14.330",
                     "end_time": "00:00:16.540"}
                ]
            },
            "2": {
                "id": 2,
                "chapter_id": 1,
                "start_time": "00:00:23.096",
                "end_time": "00:00:37.714",
                "inferred_location": "在户外休息区",
                "character_dynamics": "宋安娜车小小两人坐在椅子上吃冰淇淋...",
                "mood_and_atmosphere": "Calm",
                "dialogues": [
                    {"content": "小小，我遇到大麻烦了", "speaker": "宋安娜", "start_time": "00:00:24.290",
                     "end_time": "00:00:26.460"}
                ]
            }
        },
        "narrative_timeline": {
            "sequence": {
                "1": {"narrative_index": 1},
                "2": {"narrative_index": 2}
            }
        }
    }

    dataset = NarrativeDataset(**raw_json)
    dataset.asset_id = "CEO_Contract_Girlfriend"
    return dataset


def mock_service_config(dataset: NarrativeDataset) -> dict:
    """构造 Config 字典"""
    control = ControlParams(
        style="emotional",
        narrative_focus="general",
        target_duration_minutes=1
    )

    return {
        "asset_name": "The Martian Return",
        "asset_id": dataset.asset_id,
        "lang": "zh",
        "model": "gemini-2.5-flash",
        "rag_top_k": 2,  # 设小一点方便 Mock
        "control_params": control.model_dump(),
        "narrative_dataset": dataset
    }


# --- MOCK 类定义 (核心修复) ---

class MockGemini(GeminiProcessor):
    """模拟 LLM 生成"""

    def generate_content(self, **kwargs):
        print(f"   [MockLLM] Generating content for prompt length: {len(str(kwargs.get('prompt')))}")
        return {
            "narration_script": [
                {
                    "narration": "火星的尘埃在脚下盘旋，地球在远方闪烁。",
                    "source_scene_ids": [101]
                },
                {
                    "narration": "透过头盔的反光，能看到他眼角的泪光。",
                    "source_scene_ids": [102]
                }
            ]
        }, {"total_tokens": 100}


class TestNarrationGenerator(NarrationGenerator):
    """
    [关键] 继承并覆盖父类的 RAG 网络请求方法。
    """

    def _get_rag_corpus(self, corpus_display_name: str) -> Any:
        print(f"   [MockRAG] Skipping real corpus lookup for: {corpus_display_name}")

        class MockCorpus:
            name = "projects/mock/locations/us/ragCorpora/123456"

        return MockCorpus()

    def _retrieve_from_rag(self, corpus_name: str, query: str, top_k: int) -> List[Any]:
        print(f"   [MockRAG] Simulating retrieval for query: '{query[:30]}...' (top_k={top_k})")
        # 返回伪造的 Chunk 数据，结构必须能被 ContextEnhancer 识别 (source_uri)
        return [
            {"source_uri": "gs://bucket/_scene_101_enhanced.txt", "text": "RAG Chunk content for scene 101..."},
            {"source_uri": "gs://bucket/_scene_102_enhanced.txt", "text": "RAG Chunk content for scene 102..."}
        ]


# --- 主程序 ---

if __name__ == "__main__":
    logger = mock_logger()
    dataset = mock_narrative_dataset()
    config_dict = mock_service_config(dataset)

    print(f">> Dataset Created: {len(dataset.scenes)} scenes")
    print(f">> Config Created for Asset: {config_dict['asset_name']}")

    # 使用 TestNarrationGenerator 而不是 NarrationGenerator
    generator = TestNarrationGenerator(
        project_id="mock-project",
        location="us-central1",
        # 确保这些目录存在，或者允许代码找不到文件使用默认值
        prompts_dir=PROJECT_ROOT / "ai_services/biz_services/narration/prompts",
        metadata_dir=PROJECT_ROOT / "ai_services/biz_services/narration/metadata",
        rag_schema_path=PROJECT_ROOT / "ai_services/ai_platform/rag/metadata/schemas.json",
        logger=logger,
        work_dir=Path("/tmp"),
        gemini_processor=MockGemini(api_key="fake", logger=logger),
        cost_calculator=CostCalculator(pricing_data={}, usd_to_rmb_rate=7.2)
    )

    print(">> Generator Initialized. Executing...")

    try:
        result = generator.execute(
            asset_name=config_dict['asset_name'],
            corpus_display_name="mock-corpus",
            config=config_dict
        )
        print("\n>> SUCCESS! Result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))  # 格式化打印结果

    except Exception as e:
        print(f"\n>> FAILED: {e}")
        import traceback

        traceback.print_exc()