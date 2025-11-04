# tests/run_character_identifier.py

import sys
from pathlib import Path
import json

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# 导入“引导程序”和我们重构后的业务逻辑
from utils.local_execution_bootstrap import bootstrap_local_env_and_logger
from ai_services.analysis.character.character_identifier import CharacterIdentifier
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.common.gemini.cost_calculator_v2 import CostCalculator

def main():
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # --- [执行层的职责] 1. 定义所有路径 ---
    input_blueprint_path = project_root / "tests" / "testdata" / "narrative_blueprint_28099a52_KRe4vd0.json"
    output_dir = project_root / "output" / "character_facts"
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = project_root / 'ai_services' / 'analysis' / 'character' / 'prompts'

    # [执行层的职责] 加载业务所需的额外配置文件
    localization_path = project_root / "resource" / "localization" / "analysis" / "character_identifier.json"
    schema_path = project_root / "resource" / "metadata" / "fact_attributes.json"

    characters_to_analyze = ["车小小"]
    language_to_use = "zh"

    logger.info(f"输入文件: {input_blueprint_path}")
    logger.info(f"输出目录: {output_dir}")
    logger.info(f"使用语言: {language_to_use}")

    try:
        # --- [执行层的职责] 2. 准备所有依赖 ---
        gemini_processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY, logger=logger,
            debug_mode=settings.DEBUG, debug_dir=output_dir / "debug"
        )
        with localization_path.open('r', encoding='utf-8') as f:
            labels = json.load(f).get('zh', {})
        with schema_path.open('r', encoding='utf-8') as f:
            schema_data = json.load(f).get('zh', {})
        with input_blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        # 【核心修正】创建 CostCalculator 实例，注入其所需的配置
        cost_calculator = CostCalculator(
            pricing_data=settings.GEMINI_PRICING,
            usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
        )
        logger.info("CostCalculator 实例已创建。")

        # --- [执行层的职责] 3. 实例化业务逻辑类，并将所有依赖注入进去 ---
        identifier = CharacterIdentifier(
            gemini_processor=gemini_processor,
            cost_calculator=cost_calculator, # <-- 注入 cost_calculator 实例
            prompts_dir=prompts_dir,
            localization_path=localization_path,
            schema_path=schema_path,
            logger=logger,
            base_path=output_dir # <-- 注入服务的工作目录
        )
        logger.info("▶️ 步骤2: 组装 CharacterIdentifier 服务...")

        # --- [执行层的职责] 4. 执行核心计算 ---
        result_container = identifier.execute(
            enhanced_script_path=input_blueprint_path,
            characters_to_analyze=characters_to_analyze,
            # 将之前加载的配置作为kwargs传递
            labels=labels,
            schema_data=schema_data,
            lang=language_to_use,
            # 其他AI模型参数
            model="gemini-2.5-flash", temp=0.1, debug=settings.DEBUG
        )

        # --- [执行层的职责] 5. 保存结果 ---
        if result_container.get("status") == "success":
            # 从 result_container['data'] 中提取真正的结果和用量
            data_payload = result_container.get("data", {})
            result_to_save = data_payload.get("result", {})
            usage_to_log = data_payload.get("usage", {})

            # 使用提取出的数据进行保存
            output_path = output_dir / "character_facts_output.json"
            with output_path.open('w', encoding='utf-8') as f:
                json.dump(result_to_save, f, ensure_ascii=False, indent=2)

            logger.info(f"✅ 成功完成！结果已保存到: {output_path}")
            # 使用提取出的数据记录日志
            logger.info(f"AI 总用量: {json.dumps(usage_to_log, ensure_ascii=False, indent=2)}")
        else:
            logger.error(f"❌ 服务执行失败，返回内容: {result_container}")

    except Exception as e:
        logger.error(f"❌ 执行失败: {e}", exc_info=True)


if __name__ == "__main__":
    main()