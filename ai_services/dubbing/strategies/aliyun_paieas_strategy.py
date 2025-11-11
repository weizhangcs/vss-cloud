# 文件名: aliyun_paieas_strategy.py
# 描述: 使用阿里云PAI-EAS部署的CosyVoice服务的策略。
# 版本: 1.3 (修正了headers的定义方式，使其更规范)

import base64
import requests
from pathlib import Path
from typing import Dict, Any
from mutagen.wave import WAVE

from .base_strategy import ReplicationStrategy


class AliyunPAIEASStrategy(ReplicationStrategy):
    """通过直接HTTP请求调用PAI-EAS部署的CosyVoice服务。"""

    def __init__(self, service_url: str, token: str):
        if not service_url or not token:
            raise ValueError("PAI-EAS的服务地址或Token未配置。")

        self.service_url = service_url.rstrip('/')

        # [核心修正] 只定义一个包含通用认证信息的基础headers
        self.base_headers = {
            'Authorization': f'Bearer {token}',
        }

    def upload_reference_audio(self, audio_path: Path, text: str) -> str:
        """实现上传参考音频的逻辑。"""
        upload_url = f"{self.service_url}/api/v1/audio/reference_audio"

        if not audio_path.is_file():
            raise FileNotFoundError(f"指定的参考音频文件不存在: {audio_path}")

        files = {
            'file': (audio_path.name, open(audio_path, 'rb'), 'audio/wav'),
            'text': (None, text),
        }

        # 对于文件上传，requests会自动处理Content-Type，我们只需传入认证头
        response = requests.post(upload_url, headers=self.base_headers, files=files)
        response.raise_for_status()

        response_data = response.json()
        audio_id = response_data.get("id")
        if not audio_id:
            raise ValueError("上传参考音频后，API未返回有效的'id'。")

        return audio_id

    def synthesize(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        """实现语音合成的逻辑。"""
        synthesis_url = f"{self.service_url}/api/v1/audio/speech"

        payload = {
            "model": params.get("model", "CosyVoice2-0.5B"),
            "input": {
                "mode": params.get("mode", "natural_language_replication"),
                "reference_audio_id": params.get("reference_audio_id"),
                "text": text,
                "instruct": "用讲故事的语气，要有抑扬顿挫",
                "speed": params.get("speed", 1.0)
            },
            "stream": False,
        }

        if not payload["input"]["reference_audio_id"]:
            raise ValueError("PAI-EAS语音复刻需要一个 'reference_audio_id'。")

        # [核心修正] 基于base_headers创建本次请求专用的headers
        request_headers = self.base_headers.copy()
        request_headers['Content-Type'] = 'application/json'

        response = requests.post(synthesis_url, headers=request_headers, json=payload)
        response.raise_for_status()

        response_data = response.json()
        base64_audio_data = response_data.get("output", {}).get("audio", {}).get("data")

        if base64_audio_data:
            audio_content = base64.b64decode(base64_audio_data)
            output_path.write_bytes(audio_content)

            if output_path.stat().st_size > 0:
                audio = WAVE(str(output_path))
                return round(audio.info.length, 3)

        return 0.0