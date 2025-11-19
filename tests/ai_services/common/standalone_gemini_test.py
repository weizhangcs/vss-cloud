# tests/standalone_gemini_test.py
import os
import sys
import logging
from pathlib import Path

# --- 动态路径配置（确保导入成功）---
# 假设此脚本运行在项目根目录下的 'tests' 文件夹内
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# 导入核心依赖
try:
    from ai_services.common.gemini.gemini_processor import GeminiProcessor
    from utils.local_execution_bootstrap import bootstrap_local_env_and_logger
except ImportError as e:
    print(f"致命错误：无法导入核心依赖。请检查您的文件结构。错误: {e}")
    sys.exit(1)

# 获取日志记录器
logger = logging.getLogger(__name__)


def run_standalone_test():
    """在隔离环境中运行 Gemini API 连接测试。"""

    # 引导环境，加载 .env 文件
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 强制设置日志级别
    logger.setLevel(logging.INFO)
    logger.info("--- 独立 Gemini API 连接测试开始 (强制使用 API Key) ---")

    MODEL_NAME = "gemini-2.5-flash"
    TEST_PROMPT = "请用中文写一句关于云计算的简短口号。"

    if not settings.GOOGLE_API_KEY:
        logger.error("❌ 错误: 无法从 .env 文件中加载 GOOGLE_API_KEY。请确保该变量已设置。")
        return

    try:
        # 1. 初始化 GeminiProcessor
        # 不传入 project_id/location，强制使用 Developer API Key 模式
        processor = GeminiProcessor(
            api_key=settings.GOOGLE_API_KEY,
            logger=logger,
            debug_mode=True,  # 开启调试，方便查看请求日志
            debug_dir=project_root / "output" / "character_facts_test"  # 日志将输出到项目根目录下
        )
        logger.info(f"Processor 初始化完成。正在尝试调用模型: {MODEL_NAME}")

        # 2. 调用 generate_content
        # 注意：如果您的 GeminiProcessor 内部的 Host 强制修复有效，这里将成功。
        response_data, usage = processor.generate_content(
            model_name=MODEL_NAME,
            prompt=TEST_PROMPT,
            temperature=0.1
        )

        # 3. 报告结果
        logger.info("====================================")
        logger.info("✅ 测试通过！成功获取 API 响应。")
        logger.info(f"模型名称: {usage.get('model_used', MODEL_NAME)}")
        logger.info(f"Tokens用量: {usage.get('total_tokens')}")
        logger.info(f"返回文本片段: {response_data.get('text', '')[:50]}...")
        logger.info("请查看 standalone_debug_logs 目录下的文件，验证请求路径是否正确。")
        logger.info("====================================")

    except Exception as e:
        logger.error(f"❌ 测试失败: 调用 Gemini API 失败。")
        logger.error(f"详细错误: {e}")
        logger.error("请检查 standalone_debug_logs 目录下的请求日志文件，确认 URL 路径是否正确。")


if __name__ == "__main__":
    run_standalone_test()