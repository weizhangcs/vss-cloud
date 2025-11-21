# scripts/run_character_metrics_calculator.py

import sys
from pathlib import Path
import json

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# 导入“引导程序”和我们重构后的业务逻辑
from utils.local_execution_bootstrap import bootstrap_local_env_and_logger
from ai_services.analysis.character.character_metrics_calculator import CharacterMetricsCalculator

def main():
    """ [执行层] 负责引导环境、注入依赖、执行业务、保存结果 """
    # 1. 引导本地环境，获取配置(settings)和日志记录器(logger)
    #    (引导程序内部会加载.env文件)
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 定义输入输出路径 (请根据您的实际情况修改)
    input_blueprint_path = project_root / "tests" / "testdata" / "narrative_blueprint_28099a52_KRe4vd0.json"
    output_dir = project_root / "output" / "character_metrics"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"输入文件: {input_blueprint_path}")
    logger.info(f"输出目录: {output_dir}")

    try:
        # 2. 实例化业务逻辑类，并将 logger 注入进去
        calculator = CharacterMetricsCalculator(logger=logger)

        # 3. 准备输入数据
        with input_blueprint_path.open('r', encoding='utf-8') as f:
            blueprint_data = json.load(f)

        character_list = list(blueprint_data.get('characters', {}).keys())

        # 4. 执行核心计算 (现在它只返回一个字典)
        result_data = calculator.execute(
            blueprint_data=blueprint_data,
            character_list=character_list
        )

        # 5. [执行层的职责] 保存结果
        output_path = output_dir / "character_metrics_output.json"
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ 成功完成！结果已保存到: {output_path}")

    except Exception as e:
        logger.error(f"❌ 执行失败: {e}", exc_info=True)

if __name__ == "__main__":
    main()