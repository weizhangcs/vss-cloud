# ai_services/dubbing/strategies/google_tts_strategy.py

import logging
from pathlib import Path
from typing import Dict, Any
from google.cloud import texttospeech
from .base_strategy import TTSStrategy

logger = logging.getLogger(__name__)


class GoogleTTSStrategy(TTSStrategy):
    """
    Google Cloud TTS 策略实现。
    支持标准 TTS 模型以及 Gemini Generative TTS 模型 (带 Style Prompt)。
    """

    def synthesize(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        """
        调用 Google TTS API 合成音频。

        Args:
            text: 要合成的文本 (可能包含 [sigh] 等标记)。
            output_path: 音频保存路径。
            params: 参数字典，支持:
                - instruct: (str) 风格提示词 (e.g. "Speak in a sad tone")
                - voice_name: (str) 语音名称/人设 (e.g. "Puck", "Despina")
                - language_code: (str) 语言代码 (e.g. "cmn-CN")
                - model_name: (str) 模型版本 (e.g. "gemini-2.5-pro-tts")
                - speaking_rate: (float) 语速 (0.25 ~ 4.0)
        """
        client = texttospeech.TextToSpeechClient()

        # 1. 提取参数
        instruct = params.get("instruct")
        voice_name = params.get("voice_name", "Puck")  # 默认 Gemini 人设 Puck
        language_code = params.get("language_code", "cmn-CN")  # 默认中文
        model_name = params.get("model_name", "gemini-2.5-pro-tts")  # 默认 Gemini 模型
        speaking_rate = params.get("speaking_rate", 1.0)

        logger.info(f"[GoogleTTS] Synthesizing text (len={len(text)}) with voice '{voice_name}'...")

        # 2. 构建输入 (SynthesisInput)
        # 只有提供了 instruct 且使用 Gemini 模型时，才传入 prompt 参数
        if instruct and "gemini" in model_name.lower():
            logger.info(f"[GoogleTTS] Using Style Prompt: {instruct[:50]}...")
            synthesis_input = texttospeech.SynthesisInput(text=text, prompt=instruct)
        else:
            synthesis_input = texttospeech.SynthesisInput(text=text)

        # 3. 构建声音参数 (VoiceSelectionParams)
        try:
            # 尝试传入 model_name (需要 google-cloud-texttospeech >= 2.29.0)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name,
                model_name=model_name
            )
        except TypeError:
            # 降级兼容：如果 SDK 版本过旧不支持 model_name 参数
            logger.warning("[GoogleTTS] 'model_name' param not supported in VoiceSelectionParams. Falling back.")
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )

        # 4. 构建音频配置 (AudioConfig)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate
        )

        try:
            # 5. 发起 API 请求
            response = client.synthesize_speech(
                request={
                    "input": synthesis_input,
                    "voice": voice_params,
                    "audio_config": audio_config
                }
            )

            # 6. 保存文件
            with open(output_path, "wb") as out:
                out.write(response.audio_content)

            # 7. 计算时长 (用于后续剪辑脚本生成)
            duration = self._get_audio_duration(output_path)
            return duration

        except Exception as e:
            logger.error(f"[GoogleTTS] API call failed: {e}")
            raise

    def _get_audio_duration(self, file_path: Path) -> float:
        """使用 mutagen 读取 MP3 文件时长"""
        try:
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            return audio.info.length
        except ImportError:
            logger.warning("mutagen library not found. Returning duration 0.0.")
            return 0.0
        except Exception as e:
            logger.warning(f"Failed to calculate duration for {file_path.name}: {e}")
            return 0.0