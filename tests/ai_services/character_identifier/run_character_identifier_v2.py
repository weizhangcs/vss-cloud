import sys
import json
import shutil
import uuid
from pathlib import Path
from typing import Dict, Any

# ==============================================================================
# 0. 环境路径设置 (Path Setup)
# ==============================================================================
# 假设脚本位于 tests/ai_services/analysis/ 目录下
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]  # 回退到项目根目录
sys.path.append(str(project_root))

print(f"Project Root added to path: {project_root}")

try:
    # 1. 导入业务组件
    from ai_services.biz_services.analysis.character.character_identifier import CharacterIdentifier
    from ai_services.biz_services.narrative_dataset import NarrativeDataset

    # 2. 导入真实基础设施
    from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
    from ai_services.ai_platform.llm.cost_calculator import CostCalculator

    # 3. 导入您提供的 Bootstrap 工具
    from tests.lib.bootstrap import bootstrap_local_env_and_logger

    print("✅ Successfully imported all modules.")
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("请确保 utils/local_execution_bootstrap.py 存在且 PYTHONPATH 正确。")
    sys.exit(1)


# ==============================================================================
# 1. 准备临时资源文件 (Schema & Localization)
# ==============================================================================
def setup_temp_resources(work_dir: Path):
    """创建运行所需的本地化和 Schema 文件"""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Schema (fact_attributes.json)
    schema_content = {
        "zh": {
            "职业": {
                "display_name": "职业",
                "description": "角色的工作或社会身份",
                "type": "社会属性",
                "keywords": ["工作", "身份", "头衔"]
            },
            "性格": {
                "display_name": "性格",
                "description": "角色的内在性格特征、脾气或行事风格",
                "type": "内在属性",
                "keywords": ["脾气", "个性", "风格"]
            },
            "技能": {
                "display_name": "技能",
                "description": "角色掌握的专业技术或特殊能力",
                "type": "能力属性",
                "keywords": ["擅长", "技术", "能力"]
            }
        }
    }
    schema_path = work_dir / "fact_attributes.json"
    with schema_path.open("w", encoding="utf-8") as f:
        json.dump(schema_content, f, ensure_ascii=False)

    # 2. Localization (zh.json)
    loc_content = {
        "zh": {
            "dossier": {
                "dossier_scene_header": "--- 场景 ID: {scene_id} ---",
                "dossier_direct_header": "直接出场",
                "dossier_mentioned_header": "被提及",
                "dossier_dynamics_label": "画面动态:",
                "dossier_dialogue_header": "相关对话:",
                "dossier_dialogue_line": "  - {speaker}: {content}",
                "default_fact_type": "未分类"
            },
            "attribute_labels": {"description": "描述", "type": "类型"}
        }
    }
    loc_path = work_dir / "zh.json"
    with loc_path.open("w", encoding="utf-8") as f:
        json.dump(loc_content, f, ensure_ascii=False)

    return schema_path, loc_path


# ==============================================================================
# 2. 构造 Mock 输入数据 (NarrativeDataset)
# ==============================================================================
def create_mock_dataset(work_dir: Path) -> Path:
    """创建一个基于《火星救援》的 Mock Dataset 文件"""

    # 构造一个强烈的测试用例：包含职业自述、性格展现
    dataset_content = {
        "asset_uuid": str(uuid.uuid4()),
        "project_uuid": str(uuid.uuid4()),
        "project_metadata": {
            "asset_name": "The Martian (Mock Integration)",
            "project_name": "Real LLM Test",
            "version": "1.0",
            "issue_date": "2025-01-01",
            "annotator": "IntegrationScript",
            "description": "Testing CharacterIdentifier with Real Gemini"
        },
        "chapters": {
            "1": {"chapter_uuid": str(uuid.uuid4()), "local_id": 1, "name": "Sol 1", "scene_ids": ["101"]}
        },
        "scenes": {
            "101": {
                "scene_uuid": str(uuid.uuid4()),
                "id": 101,
                "start_time": "00:00:00.000",
                "end_time": "00:01:00.000",
                "scene_content_type": "Dialogue_Heavy",
                "inferred_location": "Ares 3 Hab",
                "character_dynamics": "Mark Watney records a video log, looking tired but determined.",
                "mood_and_atmosphere": "Desperate but Humorous",
                "dialogues": [
                    {
                        "speaker": "Mark Watney",
                        "content": "It's been 6 days since the rest of the crew thought I died. But guess what? I'm the best botanist on this planet.",
                        "start_time": "00:00:10.000",
                        "end_time": "00:00:15.000"
                    },
                    {
                        "speaker": "Mark Watney",
                        "content": "I'm going to have to science the shit out of this.",
                        "start_time": "00:00:20.000",
                        "end_time": "00:00:25.000"
                    }
                ],
                "captions": [],
                "highlights": []
            }
        },
        "narrative_storyline": {
            "root_branch_id": "main",
            "branches": {"main": {"branch_id": "main", "nodes": []}}
        }
    }

    script_path = work_dir / "enhanced_script.json"
    with script_path.open("w", encoding="utf-8") as f:
        json.dump(dataset_content, f, ensure_ascii=False)

    return script_path


# ==============================================================================
# 3. 执行集成测试
# ==============================================================================
def run_integration_test():
    # 1. Bootstrap: 加载 .env 和 配置
    print(">>> 1. Bootstrapping Environment...")
    settings, logger = bootstrap_local_env_and_logger(project_root)

    if not settings.GOOGLE_API_KEY:
        print("❌ Error: GOOGLE_API_KEY not found in .env settings.")
        return

    work_dir = Path("./temp_real_integration_work")

    # 2. 准备资源和数据
    print(">>> 2. Preparing Resources & Data...")
    schema_path, loc_path = setup_temp_resources(work_dir)
    script_path = create_mock_dataset(work_dir)

    # 3. 初始化真实基础设施 (Real Infra)
    print(">>> 3. Initializing Real Infrastructure...")

    # [Real] Gemini Processor
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,  # 来自 .env
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=work_dir / "gemini_debug"
    )

    # [Real] Cost Calculator
    # 注意：bootstrap.py 中已经将 .env 的定价参数解析为 settings.GEMINI_PRICING 字典
    cost_calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    # 4. 初始化业务服务 (Dependency Injection)
    identifier = CharacterIdentifier(
        gemini_processor=gemini_processor,  # 注入真实 Processor
        cost_calculator=cost_calculator,  # 注入真实 Calculator
        prompts_dir=work_dir,  # 这里我们在 work_dir 没放 prompt 模板，
        # *注意*：实际运行需要 prompts_dir 指向真实的 prompts 目录。
        # 假设您已将 prompts 复制到了 work_dir 或者指向项目真实路径。
        # 这里我们做一个修正：指向项目真实路径。
        localization_path=loc_path,
        schema_path=schema_path,
        logger=logger,
        base_path=work_dir
    )

    # 修正 prompts_dir 指向真实项目路径
    real_prompts_dir = project_root / "ai_services/biz_services/analysis/character/prompts"
    if real_prompts_dir.exists():
        identifier.prompts_dir = real_prompts_dir
        print(f"   Using real prompts from: {real_prompts_dir}")
    else:
        print(
            f"⚠️ Warning: Real prompts dir not found at {real_prompts_dir}. Test might fail if prompt template is missing.")

    # 5. 执行业务逻辑
    print("\n>>> 🚀 Executing Character Identification (Real API Call)...")
    try:
        result_envelope = identifier.execute(
            enhanced_script_path=script_path,
            characters_to_analyze=["Mark Watney"],
            lang="zh",
            model="gemini-2.5-flash",  # 使用 .env 中定义的 Flash 模型
            default_temp=0.1
        )

        # 6. 展示结果
        print("\n" + "=" * 30 + " REAL EXECUTION RESULT " + "=" * 30)

        data = result_envelope["data"]
        facts = data["result"]["identified_facts_by_character"].get("Mark Watney", [])
        usage = data["usage"]

        print(f"Status: {result_envelope['status']}")
        print(f"Facts Found: {len(facts)}")

        for fact in facts:
            # 打印识别出的事实
            print(f"  - [{fact.get('type', '未知')}] {fact['attribute']}: {fact['value']}")
            print(f"    Quote: {fact.get('quote', '')}")
            print(f"    Confidence: {fact.get('confidence', 0)}")

        print("-" * 30)
        print("💰 Cost Report (Real Pricing):")
        print(f"  Model: {usage.get('model_name')}")
        print(f"  Input Tokens: {usage.get('prompt_tokens')}")
        print(f"  Output Tokens: {usage.get('completion_tokens')}")
        print(f"  Total Cost: ${usage.get('total_usd', 0):.6f} (¥{usage.get('total_rmb', 0):.4f})")
        print("=" * 60)

    except Exception as e:
        print(f"❌ Execution Failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_integration_test()