# ai_services/dubbing/strategies/aliyun_paieas_strategy.py

import base64
import requests
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, List

from .base_strategy import ReplicationStrategy
from ai_services.ai_platform.tts.text_segmenter import MultilingualTextSegmenter
from ai_services.ai_platform.tts.audio_utils import merge_audio_files_ffmpeg

logger = logging.getLogger(__name__)


class AliyunPAIEASStrategy(ReplicationStrategy):
    """通过直接HTTP请求调用PAI-EAS部署的CosyVoice服务。"""

    def __init__(self, service_url: str, token: str):
        if not service_url or not token:
            raise ValueError("PAI-EAS的服务地址或Token未配置。")

        self.service_url = service_url.rstrip('/')
        self.base_headers = {
            'Authorization': f'Bearer {token}',
        }
        # [新增] 内部持有切分器，因为这是本策略特有的限制
        self.segmenter = MultilingualTextSegmenter(logger)

    # ... (upload_reference_audio 保持不变) ...
    def upload_reference_audio(self, audio_path: Path, text: str) -> str:
        # (原样保留)
        upload_url = f"{self.service_url}/api/v1/audio/reference_audio"
        if not audio_path.is_file():
            raise FileNotFoundError(f"指定的参考音频文件不存在: {audio_path}")
        files = {
            'file': (audio_path.name, open(audio_path, 'rb'), 'audio/wav'),
            'text': (None, text),
        }
        response = requests.post(upload_url, headers=self.base_headers, files=files)
        response.raise_for_status()
        return response.json().get("id")

    def synthesize(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        """
        [重构] 包含自动切分和拼接的完整合成逻辑。
        """
        # 1. 检查是否需要切分
        # CosyVoice 建议阈值 ~90 字符
        MAX_LEN = 90
        if len(text) <= MAX_LEN:
            return self._synthesize_single(text, output_path, params)

        # 2. 执行切分
        # 假设大部分场景是中文，或者可以在 params 里传入 lang
        lang = params.get("lang", "zh")
        segments = self.segmenter.segment(text, lang=lang, max_len=MAX_LEN)

        logger.info(f"[Aliyun] Text too long ({len(text)} chars). Split into {len(segments)} segments.")

        # 3. 循环合成
        temp_files = []
        try:
            # 创建一个临时子目录来存放片段，保持整洁
            work_dir = output_path.parent

            for i, seg in enumerate(segments):
                # 使用 UUID 防止并发冲突
                temp_name = f"{output_path.stem}_part_{i}_{uuid.uuid4().hex[:6]}.wav"
                temp_path = work_dir / temp_name

                self._synthesize_single(seg, temp_path, params)
                temp_files.append(temp_path)

            # 4. 合并
            return merge_audio_files_ffmpeg(temp_files, output_path)

        finally:
            # 清理临时文件
            for p in temp_files:
                if p.exists(): p.unlink()

    def _synthesize_single(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        """原 synthesize 方法的核心逻辑，负责单次 API 调用"""
        synthesis_url = f"{self.service_url}/api/v1/audio/speech"

        # ... (构造 payload 逻辑保持不变) ...
        payload = {
            "model": params.get("model", "CosyVoice2-0.5B"),
            "input": {
                "mode": params.get("mode", "natural_language_replication"),
                "reference_audio_id": params.get("reference_audio_id"),
                "text": text,
                "instruct": params.get("instruct", "用讲故事的语气，声音自然清晰"),
                "speed": params.get("speed", 1.0)
            },
            "stream": False,
        }

        request_headers = self.base_headers.copy()
        request_headers['Content-Type'] = 'application/json'

        response = requests.post(synthesis_url, headers=request_headers, json=payload)
        response.raise_for_status()

        response_data = response.json()
        base64_audio_data = response_data.get("output", {}).get("audio", {}).get("data")

        if base64_audio_data:
            audio_content = base64.b64decode(base64_audio_data)
            output_path.write_bytes(audio_content)
            # 这里的 duration 仅供参考，如果是 wav 可以读 header，否则返回 0 交给上层重新计算
            return 0.0
        return 0.0