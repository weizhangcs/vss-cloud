# tests/ai_services/dubbing/test_director_v6.py
import sys
import logging
import json
from pathlib import Path

# 环境引导
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_core_units.audio_director.director import AudioDirector
from tests.lib.bootstrap import bootstrap_local_env_and_logger


def run_test():
    settings, logger = bootstrap_local_env_and_logger(project_root)

    if not settings.GOOGLE_API_KEY:
        print("❌ 错误: 未找到 GOOGLE_API_KEY")
        return

    # 1. Init Processor
    processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=True,
        debug_dir=project_root / "shared_media" / "logs" / "director_debug"
    )

    # 2. Init Director
    prompts_dir = project_root / "ai_services/ai_core_units/audio_director/prompts"
    director = AudioDirector(processor, prompts_dir)

    # 3. Mock Data
    script = [
        {"index": 0, "narration": "天哪，这简直太不可思议了！"},
        {"index": 1, "narration": "我就知道，事情没那么简单。"}
    ]

    print(">>> 🚀 Running Audio Director...")

    # 4. Execute
    new_script, usage = director.direct_script(
        script=script,
        lang="zh",
        model="gemini-2.5-flash",
        style="humorous"
    )

    print("\n✅ Result:")
    for item in new_script:
        print(f"[{item['index']}] Instruct: {item.get('tts_instruct')}")
        print(f"    AudioText: {item.get('narration_for_audio')}")

    print(f"\n📊 Usage: {json.dumps(usage, indent=2)}")


if __name__ == "__main__":
    run_test()