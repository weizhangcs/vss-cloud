from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any


class TTSStrategy(ABC):
    """所有TTS提供商策略的抽象基类。"""

    @abstractmethod
    def synthesize(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        """
        将文本合成为音频并保存到指定路径。

        Args:
            text (str): 需要合成的文本。
            output_path (Path): 音频文件的保存路径。
            params (Dict[str, Any]): 特定于提供商的参数 (如 voice, speed 等)。

        Returns:
            float: 生成的音频文件的精确时长（秒）。

        Raises:
            Exception: 如果合成失败。
        """
        pass

# [新增] 为支持语音复刻的策略创建一个专门的接口
class ReplicationStrategy(TTSStrategy):
    """支持语音复刻的策略接口。"""

    @abstractmethod
    def upload_reference_audio(self, audio_path: Path, text: str) -> str:
        """
        上传参考音频并获取其ID。

        Args:
            audio_path (Path): 本地参考音频文件的路径。
            text (str): 参考音频对应的文本。

        Returns:
            str: 服务端返回的 reference_audio_id。
        """
        pass