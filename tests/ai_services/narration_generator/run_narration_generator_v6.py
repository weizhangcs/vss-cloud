import sys
import logging
import json
from pathlib import Path
from typing import Dict, Any, List
import uuid

# ==============================================================================
# 0. 环境路径设置 (Path Setup)
# ==============================================================================
# 当前文件: tests/ai_services/narration_generator/run_narration_generator_v6.py
# 目标根目录: 项目根目录 (即 tests 的上一级)
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]  # 向上3级: narration_generator -> ai_services -> tests -> ROOT
sys.path.append(str(project_root))

print(f"Project Root added to path: {project_root}")

try:
    # 尝试导入核心组件以验证路径
    from ai_services.biz_services.narrative_dataset import NarrativeDataset
    # [修正] 导入 v5 文件名
    from ai_services.biz_services.narration.narration_generator_v5 import NarrationGenerator
    from ai_services.biz_services.narration.schemas import NarrationServiceConfig

    print("✅ Successfully imported core modules.")
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("Please check your directory structure.")
    sys.exit(1)


# ==============================================================================
# 1. 模拟组件 (Mock Components)
# ==============================================================================

class MockGeminiProcessor:
    """模拟 LLM 处理器"""

    def generate_content(self, prompt: str, **kwargs) -> str:
        # 简单模拟返回一段 JSON 脚本
        # 注意：这里模拟了两个场景，一个是普通的，一个是倒叙的
        return """
        ```json
        {
            "narration_script": [
                {
                    "narration": "火星基地的废墟中，时间仿佛凝固。",
                    "narration_source": "Visual",
                    "source_scene_ids": [101],
                    "tts_instruct": "Slow and heavy."
                },
                {
                    "narration": "那一刻的警报声，至今仍在他脑海中回荡。",
                    "narration_source": "Visual",
                    "source_scene_ids": [102],
                    "tts_instruct": "Urgent and chaotic."
                }
            ]
        }
        ```
        """

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class MockCostCalculator:
    """模拟计费器"""

    def calculate(self, model, input_tok, output_tok):
        return {"total_usd": 0.001, "total_rmb": 0.007}


# ==============================================================================
# 2. 构造数据 (Data Construction)
# ==============================================================================

def build_strict_dataset() -> Dict[str, Any]:
    """
    构造符合 Strict Mode Schema 的 Dataset 字典。
    """
    asset_uuid = str(uuid.uuid4())
    project_uuid = str(uuid.uuid4())

    # 构造 UUIDs
    ch1_uuid = str(uuid.uuid4())
    s101_uuid = str(uuid.uuid4())
    s102_uuid = str(uuid.uuid4())

    return {
        "asset_uuid": asset_uuid,
        "project_uuid": project_uuid,
        "project_metadata": {
            "asset_name": "The Martian Return",
            "project_name": "Test Project V6",
            "version": "1.0",
            "issue_date": "2025-12-16",
            "annotator": "Tester",
            "description": "Mock Data for V6 Logic Test"
        },
        "chapters": {
            "1": {
                "chapter_uuid": ch1_uuid,
                "local_id": 1,
                "name": "The Beginning",
                "scene_ids": ["101", "102"]
            }
        },
        "scenes": {
            "101": {
                "scene_uuid": s101_uuid,
                "id": 101,
                "start_time": "00:00:00.000",
                "end_time": "00:00:10.000",  # Duration 10s
                "scene_content_type": "Establishing_Shot",
                "inferred_location": "Mars Base",
                "character_dynamics": "Wide shot of the desolate base.",
                "mood_and_atmosphere": "Quiet, Dead",
                "dialogues": [],
                "captions": [{"content": "3 Years Later", "type": "Time", "start_time": "00:00:01.000",
                              "end_time": "00:00:03.000"}],
                "highlights": []
            },
            "102": {
                "scene_uuid": s102_uuid,
                "id": 102,
                "start_time": "00:00:10.000",
                "end_time": "00:00:15.500",  # Duration 5.5s
                "scene_content_type": "Internal_Monologue",
                "inferred_location": "Cockpit",
                "character_dynamics": "Flashback of the crash. Red lights flashing.",
                "mood_and_atmosphere": "Panic",
                "dialogues": [{"speaker": "AI", "content": "Critical Alert! Eject!", "start_time": "00:00:10.500",
                               "end_time": "00:00:12.000"}],
                "captions": [],
                "highlights": [{"description": "Explosion", "type": "Action", "start_time": "00:00:14.000",
                                "end_time": "00:00:15.000", "tags": ["Fire"]}]
            }
        },
        "narrative_storyline": {
            "root_branch_id": "main",
            "branches": {
                "main": {
                    "branch_id": "main",
                    "nodes": [
                        {
                            "local_id": 101,
                            "narrative_index": 1,
                            "narrative_function": "LINEAR",
                            "ref_scene_id": None
                        },
                        {
                            "local_id": 102,
                            "narrative_index": 2,
                            # [核心测试点] 验证 ContextEnhancer 是否能识别这个倒叙
                            "narrative_function": "FLASHBACK",
                            "ref_scene_id": 101,
                            "display_label": "The Crash Memory"
                        }
                    ]
                }
            }
        }
    }


# ==============================================================================
# 3. 执行测试 (Execution)
# ==============================================================================

def run_test():
    # Setup Logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger("TestV6")

    # 1. 自动定位资源目录 (避免 FileNotFoundError)
    # 资源位于: ai_services/biz_services/narration/prompts
    narration_root = project_root / "ai_services/biz_services/narration"
    prompts_dir = narration_root / "prompts"
    metadata_dir = narration_root / "metadata"

    # 验证目录是否存在
    if not prompts_dir.exists():
        logger.warning(f"⚠️ Prompts dir not found at {prompts_dir}, using mock path.")
    if not metadata_dir.exists():
        logger.warning(f"⚠️ Metadata dir not found at {metadata_dir}, using mock path.")

    # 2. 初始化 Generator
    generator = NarrationGenerator(
        project_id="mock-project",
        location="us-central1",
        prompts_dir=prompts_dir,
        metadata_dir=metadata_dir,
        rag_schema_path=Path("./schema"),  # Mock path
        logger=logger,
        work_dir=Path("./work"),
        gemini_processor=MockGeminiProcessor(),
        cost_calculator=MockCostCalculator()
    )

    # 3. 准备 Mock 输入
    mock_chunks = [
        {"source_uri": "gs://bucket/assets/v6_test/_scene_101_enhanced.txt", "content": "dummy content"},
        {"source_uri": "gs://bucket/assets/v6_test/_scene_102_enhanced.txt", "content": "dummy content"}
    ]

    # 4. 准备 Config (Payload)
    dataset_dict = build_strict_dataset()

    config_payload = {
        "asset_name": "The Martian Return",
        "lang": "zh",
        "model": "gemini-pro",
        "control_params": {
            "style": "Cinematic",
            "perspective": "third_person",
            "target_duration_minutes": 1.0,
            "speaking_rate": 4.5,
            "narrative_focus": "custom",
            "custom_prompts": {"narrative_focus": "Focus on the isolation."}
        },
        # [Strict] 必须包含 narrative_dataset
        "narrative_dataset": dataset_dict
    }

    print("\n>>> 🚀 Starting Narration Generator V6 Test (Mock Mode)...\n")

    try:
        # --- Step 1: Config Validation (Dataset Initialization) ---
        logger.info("[Step 1] Validating Config & Initializing Dataset...")
        # 这一步会触发 NarrativeDataset 的 Pydantic 校验，并自动计算 duration
        service_config = generator._validate_config(config_payload)

        # 验证计算字段
        scene_101 = service_config.narrative_dataset.scenes["101"]
        scene_102 = service_config.narrative_dataset.scenes["102"]

        logger.info(f"✅ Dataset Validated.")
        logger.info(f"   Scene 101 Duration: {scene_101.duration}s (Expected 10.0)")
        logger.info(f"   Scene 102 Duration: {scene_102.duration}s (Expected 5.5)")

        assert scene_101.duration == 10.0
        assert scene_102.duration == 5.5

        # --- Step 2: Context Enhancer ---
        logger.info("[Step 2] Enhancing Context (Reconstruction)...")
        # 这一步会调用 ContextEnhancer，测试其是否能读取 Storyline 逻辑
        context = generator._prepare_context(mock_chunks, service_config)

        print("\n" + "=" * 20 + " GENERATED CONTEXT SNAPSHOT " + "=" * 20)
        print(context)
        print("=" * 60 + "\n")

        # 验证 Context 内容
        if "FLASHBACK" in context and "relative to Scene 101" in context:
            logger.info("✅ SUCCESS: FLASHBACK logic correctly injected into context.")
        else:
            logger.error("❌ FAILURE: FLASHBACK logic missing from context.")

        if "Text: 3 Years Later" in context:
            logger.info("✅ SUCCESS: Caption '3 Years Later' correctly injected.")

        # --- Step 3: Prompt Construction ---
        logger.info("[Step 3] Constructing Prompt...")
        prompt = generator._construct_prompt(context, service_config)
        logger.info(f"✅ Prompt assembled (Length: {len(prompt)} chars).")

        # --- Step 4: Generation (Mock LLM) ---
        logger.info("[Step 4] Mocking LLM Generation...")
        # 模拟父类 BaseRagGenerator.generate 的部分逻辑
        llm_response_str = generator.gemini_processor.generate_content(prompt)
        llm_response_json = json.loads(llm_response_str.replace("```json", "").replace("```", ""))

        # --- Step 5: Post Process (Validator) ---
        logger.info("[Step 5] Post-Processing (Validator)...")
        # 这一步测试 Generator 是否正确地将 Dataset Dump 传给了 Validator
        # 且 Validator 能否正确校验无帧的 duration
        final_result = generator._post_process(
            llm_response_json,
            service_config,
            {"total_tokens": 100},
            rag_context=context
        )

        script = final_result['narration_script']
        logger.info(f"✅ Pipeline Complete. Generated {len(script)} snippets.")

        # 检查 Validator 结果
        s1 = script[0]  # Scene 101, Duration 10s. Text: "火星基地的废墟中，时间仿佛凝固。" (15字)

        # 安全获取 metadata (防止为 None)
        meta1 = s1.get('metadata', {})
        validation_msg = meta1.get('validation_error') or 'Passed'
        pred_dur = meta1.get('pred_audio_duration', 'N/A')
        limit = meta1.get('duration_limit', 'N/A')

        logger.info(f"   Snippet 101 Validation: {validation_msg}")
        logger.info(f"   Snippet 101 Duration: Pred={pred_dur}s / Limit={limit}s")

        s2 = script[1]
        meta2 = s2.get('metadata', {})
        validation_msg2 = meta2.get('validation_error') or 'Passed'
        logger.info(f"   Snippet 102 Validation: {validation_msg2}")

    except Exception as e:
        logger.error(f"❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test()