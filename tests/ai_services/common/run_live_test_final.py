# run_live_test_v1_final.py
# 描述: 最终架构集成测试。验证 V1 Processor (genai) 与 V3 Calculator (修正价格) 的数据流。

import sys
from pathlib import Path
import json
import logging
from datetime import datetime
from decouple import config
from typing import Dict, Any

# 确保项目根目录在 Python 路径中
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

# --- 导入 V1 Processor (genai) 和 V3 Calculator (价格修正) ---
# 注意: 确保 cost_calculator_v3.py 文件存在于你的本地目录中
try:
    from ai_services.common.gemini.gemini_processor import GeminiProcessor
    from ai_services.common.gemini.cost_calculator import CostCalculator as CostCalculator
except ImportError as e:
    print(f"致命错误: 导入失败。请确保文件存在: {e}")
    sys.exit(1)

# --- 配置占位符 (请在此处修改为你真实的密钥) ---
# ⚠️ 替换为你的 GOOGLE_API_KEY
LIVE_API_KEY = ""

# 调试日志将写入这个目录
LIVE_DEBUG_DIR = project_root / "shared_media" / "logs" / "live_gemini_test_output_v1_final"


# 配置日志记录器
def setup_live_logger():
    logger = logging.getLogger("live_test_runner_v1_final")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(ch)
    return logger


def load_settings() -> Dict[str, Any]:
    """从 .env 文件加载所有必要的 API Key 和价格配置"""
    try:
        # 假设所有 2.5 模型的配置都已在 .env 中正确定义 (V3 标准)
        pricing_data = {
            "gemini-2.5-pro": {
                "threshold": config('GEMINI_2_5_PRO_THRESHOLD_TOKENS', cast=int),
                "standard": {
                    "input": config('GEMINI_2_5_PRO_INPUT_USD_STANDARD', cast=float),
                    "output": config('GEMINI_2_5_PRO_OUTPUT_USD_STANDARD', cast=float),
                },
                "long": {
                    "input": config('GEMINI_2_5_PRO_INPUT_USD_LONG', cast=float),
                    "output": config('GEMINI_2_5_PRO_OUTPUT_USD_LONG', cast=float),
                }
            },
            "gemini-2.5-flash": {
                "threshold": config('GEMINI_2_5_FLASH_THRESHOLD_TOKENS', cast=int),
                "standard": {
                    "input": config('GEMINI_2_5_FLASH_INPUT_USD_STANDARD', cast=float),
                    "output": config('GEMINI_2_5_FLASH_OUTPUT_USD_STANDARD', cast=float),
                },
            },
        }

        return {
            "api_key": LIVE_API_KEY,
            "pricing": pricing_data,
            "usd_to_rmb_rate": config('USD_TO_RMB_EXCHANGE_RATE', cast=float)
        }
    except Exception as e:
        print(f"🔴 错误: 无法从 .env 加载配置。请确保 .env 文件存在且所有价格变量已定义. 错误: {e}")
        sys.exit(1)


def run_live_test_v1_final():
    """执行 V1 Processor (genai) 与 V3 Calculator (价格修正) 的实时集成测试"""

    if LIVE_API_KEY == "YOUR_REAL_GOOGLE_API_KEY":
        print("🔴 错误: 请先在 run_live_test_v1_final.py 文件中设置 LIVE_API_KEY。")
        return

    # 1. 初始化环境和配置
    logger = setup_live_logger()
    settings = load_settings()
    LIVE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("🟢 实时测试启动. Processor: V1 (genai), Calculator: V3 (Vertex Price)")

    try:
        # 2. 实例化 GeminiProcessor (V1 - 使用 API Key)
        processor = GeminiProcessor(
            api_key=settings['api_key'],
            logger=logger,
            debug_mode=True,
            debug_dir=LIVE_DEBUG_DIR
        )

        # 3. 实例化 CostCalculator V3 (使用最新的价格模型)
        calculator = CostCalculator(
            pricing_data=settings['pricing'],
            usd_to_rmb_rate=settings['usd_to_rmb_rate']
        )

        # 4. 执行同步 API 调用 (测试短上下文/平价模型)
        logger.info("-" * 50)
        logger.info("▶️ 正在执行同步 API 调用 (Gemini 2.5 Flash)...")

        test_prompt = (
            "Explain the difference between Python's 'asyncio.run()' and 'asyncio.create_task()' "
            "in 3 sentences, and format your entire response as a single JSON object "
            "with keys 'summary' (string) and 'is_async' (boolean)."
        )

        parsed_data, usage = processor.generate_content(
            model_name="gemini-2.5-pro",  # 使用 Flash 模型进行测试
            prompt=test_prompt,
            temperature=0.2
        )

        # 5. 计算成本
        # V3 Calculator 会使用 usage['model_used'] 进行价格查找
        cost_report = calculator.calculate(model_name="fallback", usage_data=usage)

        # 6. 打印结果和用量报告
        logger.info("✅ API 调用成功。")
        logger.info(f"--- 最终用量报告 (V1 Processor) --- \n{json.dumps(usage, indent=2, ensure_ascii=False)}")
        logger.info(f"--- 成本核算 (V3 Calculator) ---")
        logger.info(f"  > Model: {usage['model_used']}")
        logger.info(f"  > Cost (USD): {cost_report['cost_usd']:.6f}")
        logger.info(f"  > Cost (RMB): {cost_report['cost_rmb']:.4f}")
        logger.info("-" * 50)


    except Exception as e:
        logger.error(f"🔴 致命错误: 实时测试失败: {e}", exc_info=True)


if __name__ == "__main__":
    run_live_test_v1_final()