# 文件名: run_character_pipeline.py
# 描述: [重构后] “人物分析线编排器”的测试客户端，现在作为 Composition Root。
# 版本: 2.0 (Decoupled)

import argparse
from pathlib import Path
import sys
import traceback
import json

# ==================== 依赖导入区 ====================
# 导入需要实例化的类
from visify_ae.application.services.analysis.character.character_identifier import CharacterIdentifier
from visify_ae.application.infrastructure.ai_proxy.Gemini_processor import GeminiProcessor
from visify_ae.application.infrastructure.ai_proxy.cost_calculator import CostCalculator
# 导入配置加载器
from visify_ae.application.infrastructure.config import get_config


# ====================================================

class CharacterPipelineClient:
    """
    [重构后] 一个用于触发分析流程的客户端。
    它的核心方法 _run_process 现在是“组合根”，负责实例化并注入所有依赖。
    """

    @staticmethod
    def run_test_case():
        """IDE调试专用入口"""
        print("\n" + "=" * 20 + " 🚀 Character Analysis Pipeline 测试用例 (解耦版) " + "=" * 20)
        # 测试配置保持不变
        TEST_CONFIG = {
            "enhanced_script_path": Path(
                r"C:\Users\wei_z\Desktop\output\narrative_blueprint_28099a52_KRe4vd0.json"),
            "output_dir": Path(
                r"D:\DevProjects\PyCharmProjects\visify-ae\debug\test_outputs\analysis\character\车小小"),
            "lang": "zh",
            "top_n": 1,
            "characters_to_analyze": ["车小小"],  # 为方便演示，直接在这里指定
            "identifier_config": {
                "model": "gemini-1.5-flash-latest",
                "temp": 0.1,
                "debug": True
            }
        }
        try:
            CharacterPipelineClient._run_process(**TEST_CONFIG)
        except Exception as e:
            print(f"❌ 测试用例执行失败: {e}", file=sys.stderr);
            traceback.print_exc();
            sys.exit(1)

    @staticmethod
    def _run_process(**kwargs):
        """
        [重构后] 核心处理流程，现在是 "Composition Root"。
        它负责读取全局配置，并用它来创建和"组装"所有服务及其依赖。
        """
        # ==================== 依赖组装区 (Composition Root) ====================
        print("▶️ 步骤1: 加载配置并创建依赖实例...")

        # 1. 在应用程序的入口处，加载一次配置
        config = get_config()

        # 2. 创建底层依赖实例
        identifier_config = kwargs.get("identifier_config", {})

        # 创建 GeminiProcessor (可传入 debug 参数)
        gemini_processor = GeminiProcessor(debug_mode=identifier_config.get("debug", False))

        # 创建 CostCalculator，并注入它需要的数据
        cost_calculator = CostCalculator(
            pricing_data=config.pricing,
            usd_to_rmb_rate=config.usd_to_rmb_rate
        )

        # 3. 计算并准备好所有路径依赖
        resource_dir = config.get_resource_dir()
        service_name = CharacterIdentifier.SERVICE_NAME

        char_identifier_prompts_dir = resource_dir / "prompts" / "analysis" / "character"
        char_identifier_loc_path = resource_dir / "localization" / "analysis" / f"{service_name}.json"
        fact_schema_path = resource_dir / "metadata" / "fact_attributes.json"

        # 4. 实例化 CharacterIdentifier 服务，注入所有准备好的依赖
        print("▶️ 步骤2: 组装 CharacterIdentifier 服务...")
        character_identifier_service = CharacterIdentifier(
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator,
            prompts_dir=char_identifier_prompts_dir,
            localization_path=char_identifier_loc_path,
            schema_path=fact_schema_path,
            base_path=kwargs.get("output_dir")
        )
        # ========================= 组装区结束 =========================

        # ========================= 业务执行区 =========================
        print(f"\n🚀 步骤3: 执行服务核心逻辑...")

        # 从 kwargs 获取 execute 方法需要的参数
        result = character_identifier_service.execute(
            enhanced_script_path=kwargs["enhanced_script_path"],
            characters_to_analyze=kwargs["characters_to_analyze"],
            lang=kwargs.get("lang", "zh"),
            # 将 identifier_config 中的参数透传给 execute
            **identifier_config
        )
        # ==============================================================

        print("\n" + "=" * 20 + " ✅ 服务执行成功 " + "=" * 20)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    # run_from_console 方法也应遵循类似的模式来组装依赖
    @staticmethod
    def run_from_console():
        # ... (解析命令行参数) ...
        # args = parser.parse_args()
        # try:
        #    # 将 vars(args) 传递给 _run_process，组装逻辑是复用的
        #    CharacterPipelineClient._run_process(**vars(args))
        # ...
        pass


if __name__ == "__main__":
    # 为了演示，我们只运行测试用例
    CharacterPipelineClient.run_test_case()