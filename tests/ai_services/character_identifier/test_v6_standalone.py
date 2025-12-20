# tests/ai_services/character_identifier/test_v6_standalone.py
import sys
import logging
import json
from pathlib import Path

# --- 1. 环境引导 ---
# 确保项目根目录在 sys.path 中
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.analysis.character.character_identifier import CharacterIdentifier
from tests.lib.bootstrap import bootstrap_local_env_and_logger


def run_test():
    # 1. 加载环境变量 (.env) 和 模拟 Logger
    settings, logger = bootstrap_local_env_and_logger(project_root)

    if not settings.GOOGLE_API_KEY:
        print("❌ 错误: 未找到 GOOGLE_API_KEY，请检查 .env 文件")
        return

    print("\n>>> [Step 1] 初始化基础设施 (V6 Infra)...")

    # 初始化 Processor (V2)
    processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=True,
        debug_dir=project_root / "shared_media" / "logs" / "char_debug"
    )

    # 初始化 Calculator (V4)
    calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    print(">>> [Step 2] 准备测试数据...")
    # 这里的路径指向您项目中真实存在的测试文件
    script_path = project_root / "tests/testdata/mock.json"

    if not script_path.exists():
        print(f"❌ 错误: 测试数据文件不存在: {script_path}")
        return

    print(">>> [Step 3] 初始化业务服务 (CharacterIdentifier)...")
    identifier = CharacterIdentifier(
        gemini_processor=processor,
        cost_calculator=calculator,
        prompts_dir=project_root / "ai_services/biz_services/analysis/character/prompts",
        localization_path=project_root / "ai_services/biz_services/analysis/character/localization/character_identifier.json",
        schema_path=project_root / "ai_services/biz_services/analysis/character/metadata/fact_attributes.json",
        logger=logger,
        base_path=project_root / "shared_media" / "tmp"
    )

    print(">>> [Step 4] 执行核心逻辑 (Schema-First Inference)...")
    try:
        # 模拟一次调用
        result_envelope = identifier.execute(
            enhanced_script_path=script_path,
            characters_to_analyze=["李明"],  # 替换为您数据中真实的角色名
            lang="zh",
            model="gemini-2.5-flash",
            default_temp=0.1
        )

        print("\n✅ 测试成功! 结果如下:")

        # 打印 Usage (验证 CostCalculator 是否工作)
        usage = result_envelope['data']['usage']
        print(f"💰 成本报告: ${usage.get('cost_usd', 0):.6f} / ¥{usage.get('cost_rmb', 0):.4f}")
        print(f"📊 Token消耗: {usage.get('total_tokens')}")

        # 打印 Facts (验证 Schema 是否生效)
        facts = result_envelope['data']['result']['identified_facts_by_character'].get("李明", [])
        print(f"\n🔍 识别到的事实 ({len(facts)}条):")
        for i, fact in enumerate(facts[:5]):  # 只打印前5条
            print(f"  {i + 1}. [{fact.get('attribute')}] {fact.get('value')}")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test()