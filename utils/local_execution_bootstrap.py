# utils/local_execution_bootstrap.py

import logging
from pathlib import Path
from dotenv import load_dotenv
from types import SimpleNamespace

def bootstrap_local_env_and_logger(project_root: Path):
    """
    [本地测试专用] 引导程序。
    负责加载.env文件，创建一个模拟的settings对象，并返回一个logger。
    """
    # 1. 加载 .env 文件
    env_path = project_root / '.env'
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
        print(f"Loaded environment variables from: {env_path}")
    else:
        print(f"Warning: .env file not found at {env_path}")

    # 2. 创建一个模拟的 settings 对象
    #    这里我们手动模拟 core/settings.py 中的逻辑
    #    注意：这部分需要与 core/settings.py 保持同步
    from decouple import config
    mock_settings = SimpleNamespace()
    mock_settings.GEMINI_PRICING = {
        "gemini-2.5-pro": {
            "threshold": config('GEMINI_1_5_PRO_THRESHOLD_TOKENS', cast=int, default=128000),
            "standard": {"input": config('GEMINI_1_5_PRO_INPUT_USD_STANDARD', cast=float, default=3.50),
                         "output": config('GEMINI_1_5_PRO_OUTPUT_USD_STANDARD', cast=float, default=10.50)},
            "long": {"input": config('GEMINI_1_5_PRO_INPUT_USD_LONG', cast=float, default=7.00),
                     "output": config('GEMINI_1_5_PRO_OUTPUT_USD_LONG', cast=float, default=21.00)}
        },
        "gemini-2.5-flash": {
            "threshold": config('GEMINI_1_5_FLASH_THRESHOLD_TOKENS', cast=int, default=128000),
            "standard": {"input": config('GEMINI_1_5_FLASH_INPUT_USD_STANDARD', cast=float, default=0.075),
                         "output": config('GEMINI_1_5_FLASH_OUTPUT_USD_STANDARD', cast=float, default=0.30)},
            "long": {"input": config('GEMINI_1_5_FLASH_INPUT_USD_LONG', cast=float, default=0.15),
                     "output": config('GEMINI_1_5_FLASH_OUTPUT_USD_LONG', cast=float, default=0.60)}
        }
    }
    mock_settings.GOOGLE_API_KEY = config('GOOGLE_API_KEY', default='')
    mock_settings.DEBUG = config('DEBUG', default=True, cast=bool)
    mock_settings.USD_TO_RMB_EXCHANGE_RATE = config('USD_TO_RMB_EXCHANGE_RATE', cast=float, default=7.25)  # <-- 已添加

    # 3. 创建一个标准的 Python logger
    logger = logging.getLogger("local_test_runner")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return mock_settings, logger