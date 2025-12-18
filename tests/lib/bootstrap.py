# tests/lib/bootstrap.py

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

    # --- [Category 1] 基础配置 ---
    mock_settings.DEBUG = config('DEBUG', default=True, cast=bool)

    # --- [Category 2] Google Cloud & AI Services ---
    mock_settings.GOOGLE_API_KEY = config('GOOGLE_API_KEY', default='')
    mock_settings.GOOGLE_CLOUD_PROJECT = config('GOOGLE_CLOUD_PROJECT', default='')  # <--- [新增]
    mock_settings.GOOGLE_CLOUD_LOCATION = config('GOOGLE_CLOUD_LOCATION', default='')  # <--- [新增]
    mock_settings.GCS_DEFAULT_BUCKET = config('GCS_DEFAULT_BUCKET', default='')  # <--- [新增]

    mock_settings.PAI_EAS_SERVICE_URL = config('PAI_EAS_SERVICE_URL', default='')  # <--- [新增]
    mock_settings.PAI_EAS_TOKEN = config('PAI_EAS_TOKEN', default='')  # <--- [新增]

    # --- [Category 3] 业务配置与定价 ---
    mock_settings.USD_TO_RMB_EXCHANGE_RATE = config('USD_TO_RMB_EXCHANGE_RATE', cast=float, default=7.25)

    # 更新为适配 .env 新结构的 2.5 系列定价
    mock_settings.GEMINI_PRICING = {
        "gemini-2.5-pro": {
            "threshold": config('GEMINI_2_5_PRO_THRESHOLD_TOKENS', cast=int, default=200000),
            "standard": {
                "input": config('GEMINI_2_5_PRO_INPUT_USD_STANDARD', cast=float, default=1.25),
                "output": config('GEMINI_2_5_PRO_OUTPUT_USD_STANDARD', cast=float, default=10.00)
            },
            "long": {
                "input": config('GEMINI_2_5_PRO_INPUT_USD_LONG', cast=float, default=2.50),
                "output": config('GEMINI_2_5_PRO_OUTPUT_USD_LONG', cast=float, default=15.00)
            }
        },
        "gemini-2.5-flash": {
            "threshold": config('GEMINI_2_5_FLASH_THRESHOLD_TOKENS', cast=int, default=9999999),
            "standard": {
                "input": config('GEMINI_2_5_FLASH_INPUT_USD_STANDARD', cast=float, default=0.30),
                "output": config('GEMINI_2_5_FLASH_OUTPUT_USD_STANDARD', cast=float, default=2.50)
            },
            # Flash 无长上下文分层，无需 long 键
        },
        "gemini-2.5-flash-lite": {
            "threshold": config('GEMINI_2_5_FLASH_LITE_THRESHOLD_TOKENS', cast=int, default=9999999),
            "standard": {
                "input": config('GEMINI_2_5_FLASH_LITE_INPUT_USD_STANDARD', cast=float, default=0.10),
                "output": config('GEMINI_2_5_FLASH_LITE_OUTPUT_USD_STANDARD', cast=float, default=0.10)
            }
        }
    }

    # 3. 创建一个标准的 Python logger
    logger = logging.getLogger("local_test_runner")
    logger.setLevel(logging.INFO)
    # 防止重复添加 Handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return mock_settings, logger