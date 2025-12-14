# tests/dubbing/run_dubbing_engine.py
# 描述: [Engine 集成测试] 验证切分、风格注入及 FFmpeg 拼接逻辑
#       不依赖真实 API，使用 Mock 策略生成音频。

import sys
import json
import wave
import struct
import math
import random
from pathlib import Path
from typing import Dict, Any

# 1. 路径引导 [修正]
# 文件位于: VisifyStoryStudio/tests/dubbing/run_dubbing_engine.py
# parents[0] = dubbing
# parents[1] = tests
# parents[2] = VisifyStoryStudio (Project Root)
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from tests.lib.bootstrap import bootstrap_local_env_and_logger
from ai_services.dubbing.dubbing_engine import DubbingEngine
from ai_services.dubbing.strategies.base_strategy import TTSStrategy


# ==============================================================================
# 1. Mock 策略实现 (生成真实的 WAV 文件以供 FFmpeg 拼接)
# ==============================================================================
class MockTTSStrategy(TTSStrategy):
    """
    模拟 TTS 策略。
    不调用 API，而是生成一段指定时长的正弦波 WAV 文件，用于测试拼接功能。
    """

    def synthesize(self, text: str, output_path: Path, params: Dict[str, Any]) -> float:
        print(f"    [MockTTS] Synthesizing segment: '{text[:15]}...'")

        # 检查风格指令是否成功注入
        instruct = params.get("instruct")
        if instruct:
            print(f"    [MockTTS] -> Received Instruct: {instruct}")

        # 模拟生成时长 (0.5s - 2.0s)
        duration = random.uniform(0.5, 2.0)
        self._generate_sine_wave(output_path, duration)
        return duration

    def _generate_sine_wave(self, filepath: Path, duration: float, frequency: int = 440, framerate: int = 44100):
        """生成一段简单的正弦波音频文件"""
        n_frames = int(duration * framerate)
        data = []
        for i in range(n_frames):
            value = int(32767.0 * math.sin(2.0 * math.pi * frequency * i / framerate))
            data.append(struct.pack('<h', value))

        with wave.open(str(filepath), 'w') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(framerate)
            f.writeframes(b''.join(data))


# ==============================================================================
# 2. 主测试逻辑
# ==============================================================================
def main():
    # 2. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 3. 准备路径 (修正后应位于项目内的 shared_media)
    work_dir = project_root / "shared_media" / "tmp" / "dubbing_engine_test"

    # 清理旧数据
    if work_dir.exists():
        import shutil
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"工作目录: {work_dir}")

    # 4. 准备 Mock 数据
    long_text = (
        "这是一个非常长的测试句子，用于验证切分器的逻辑是否正常工作。"
        "我们希望这段话被切分成多个小片段，然后分别送入模拟的TTS引擎生成音频。"
        "生成完毕后，FFmpeg应该能够将这些零散的片段无缝拼接成一个完整的文件。"
        "如果这一步成功了，说明我们的引擎已经具备了处理长篇解说词的能力，"
        "无论多长的文案都能从容应对！"
    )

    mock_narration_data = {
        "config_snapshot": {
            "control_params": {"style": "humorous"}  # 测试风格注入
        },
        "narration_script": [
            {
                "narration": "大家好，我是测试员。",
                "source_scene_ids": [1]
            },
            {
                "narration": long_text,  # 长难句
                "source_scene_ids": [2, 3]
            }
        ]
    }

    narration_path = work_dir / "input_narration.json"
    with narration_path.open("w", encoding="utf-8") as f:
        json.dump(mock_narration_data, f, ensure_ascii=False, indent=2)

    # 5. 构造 Mock 模板配置
    mock_templates = {
        "mock_template": {
            "provider": "mock_provider",
            "audio_format": "wav",  # 使用 wav 避免 ffmpeg 编解码耗时
            "params": {"speed": 1.2}
        }
    }

    # 6. 初始化引擎
    strategies = {"mock_provider": MockTTSStrategy()}

    metadata_dir = project_root / "ai_services" / "dubbing" / "metadata"

    engine = DubbingEngine(
        logger=logger,
        work_dir=work_dir,
        strategies=strategies,
        templates=mock_templates,
        metadata_dir=metadata_dir,
        shared_root_path=project_root / "shared_media"
    )

    # 7. 执行生成
    logger.info(">>> 开始执行 DubbingEngine 测试...")

    try:
        # 我们传入 style='suspense' 来覆盖 input_data 中的 'humorous'，验证参数优先级
        result = engine.execute(
            narration_path=narration_path,
            template_name="mock_template",
            style="suspense",
            lang="zh"
        )

        logger.info("✅ 执行完成！")

        # 8. 结果验证
        script = result.get("dubbing_script", [])
        logger.info(f"生成了 {len(script)} 个音频条目")

        for i, item in enumerate(script):
            audio_path = item.get("audio_file_path")
            duration = item.get("duration_seconds")
            error = item.get("error")

            print(f"\n--- Clip #{i + 1} ---")
            if error:
                print(f"❌ 失败: {error}")
            else:
                print(f"✅ 成功: {audio_path}")
                print(f"   时长: {duration}s")

                # 验证文件是否真实存在且大小正常
                # 注意：audio_path 是相对路径，需要拼上 project_root / shared_media
                full_path = project_root / "shared_media" / audio_path
                if full_path.exists() and full_path.stat().st_size > 100:
                    print(f"   文件检查: OK ({full_path.stat().st_size} bytes)")
                else:
                    print(f"   ❌ 文件检查失败: 文件不存在或为空 (Path: {full_path})")

    except Exception as e:
        logger.error(f"❌ 测试过程中发生异常: {e}", exc_info=True)


if __name__ == "__main__":
    main()