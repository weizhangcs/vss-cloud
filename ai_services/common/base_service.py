# src/visify_ae/application/services/Base_service.py
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from enum import IntEnum
from typing import Callable, Optional, Dict, Union
import logging
from pathlib import Path
from visify_ae.application.infrastructure.config import get_config
from visify_ae.application.infrastructure.logging.logger import setup_logger


class ServiceStatus(IntEnum):
    INITIALIZED = 0
    PROCESSING = 1
    COMPLETE = 2
    ERROR = 3


class BaseService(ABC):
    """服务基类（路径管理逻辑已大幅简化和修正）"""
    SERVICE_NAME: str = "base_service"
    HAS_OWN_DATADIR: bool = True

    def __init__(self, base_path: Union[str, Path, None] = None):
        self._config = get_config()
        self._logger = setup_logger(f"service.{self.__class__.__name__}")
        self._status_handler: Optional[Callable[[int, dict], None]] = None

        # 服务标识
        self._service_name = self.__class__.__name__.lower()

        # 唯一的职责：解析并提供工作目录 (work_dir)
        self._work_dir = self._resolve_work_dir(base_path)

    def _resolve_work_dir(self, custom_path: Union[str, Path, None]) -> Path:
        """
        解析工作目录 (优先级: 方法参数 > Config文件)。
        这是BaseService唯一需要负责的路径逻辑。
        """
        """
        [已升级] 解析工作目录，现在会根据HAS_OWN_DATADIR属性决定是否创建子目录。
        """
        if custom_path:
            path = Path(custom_path).resolve()
        else:
            # --- 核心修改 ---
            if self.HAS_OWN_DATADIR:
                # 对于需要自己目录的服务，正常获取带服务名的路径
                path = self._config.get_service_work_dir(self._service_name)
            else:
                # 对于不需要自己目录的服务，只获取基础数据路径
                path = self._config.get_service_work_dir()
            # --- 修改结束 ---

        path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Service [{self._service_name}] work directory is set to: {path}")
        return path

    def _aggregate_usage(self, total_usage: dict, new_usage: dict):
        """
        一个通用的、动态的usage聚合方法。
        它会遍历新usage中的所有键，对所有数字类型的值进行累加，
        并追踪全局的开始和结束时间。
        """
        if not new_usage:
            return

        # 累加所有数字类型的指标
        for key, value in new_usage.items():
            if isinstance(value, (int, float)):
                total_usage[key] = total_usage.get(key, 0) + value

        # 追踪整个会话的最早开始时间和最晚结束时间
        if 'start_time_utc' in new_usage:
            if 'session_start_time' not in total_usage or new_usage['start_time_utc'] < total_usage[
                'session_start_time']:
                total_usage['session_start_time'] = new_usage['start_time_utc']

        if 'end_time_utc' in new_usage:
            if 'session_end_time' not in total_usage or new_usage['end_time_utc'] > total_usage['session_end_time']:
                total_usage['session_end_time'] = new_usage['end_time_utc']

    def _save_result(self, result: Dict, output_dir_override: Union[str, Path, None], prefix: str) -> Path:
        """一个通用的结果保存方法"""
        directory = Path(output_dir_override) if output_dir_override else self.work_dir
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = directory / f"{prefix}_{timestamp}.json"
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        self.logger.info(f"文件已保存至: {output_path}")
        return output_path

    @property
    def work_dir(self) -> Path:
        """提供对服务专属工作目录的只读访问。"""
        return self._work_dir

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    def status_handler(self) -> Optional[Callable[[int, dict], None]]:
        return self._status_handler

    @status_handler.setter
    def status_handler(self, handler: Callable[[int, dict], None]):
        if handler is not None and not callable(handler):
            raise ValueError("状态处理器必须是可调用对象")
        self._status_handler = handler

    def _notify_status(self, status: ServiceStatus, data: dict = None):
        if callable(self._status_handler):
            try:
                self._status_handler(int(status), data or {})
            except Exception as e:
                self.logger.error(f"状态通知失败: {e}", exc_info=True)

    @abstractmethod
    def execute(self, *args, **kwargs):
        pass
