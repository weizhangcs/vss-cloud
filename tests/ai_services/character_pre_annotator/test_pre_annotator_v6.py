# tests/ai_services/character_pre_annotator/test_pre_annotator_v6.py

import sys
import json
import logging
from pathlib import Path

# ==============================================================================
# 0. 环境路径设置
# ==============================================================================
# 定位到项目根目录 (tests/../..)
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]
sys.path.append(str(project_root))

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.character_pre_annotator.service import CharacterPreAnnotatorService
from tests.lib.bootstrap import bootstrap_local_env_and_logger


def create_mock_srt(work_dir: Path) -> Path:

    srt_path = work_dir / "mock_dialogue.srt"
    return srt_path


def run_test():
    # 1. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    if not settings.GOOGLE_API_KEY:
        print("❌ 错误: 未找到 GOOGLE_API_KEY")
        return

    # 准备工作目录
    work_dir = project_root / "shared_media" / "tmp" / "pre_annotator_test"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2. 准备数据
    srt_path = create_mock_srt(work_dir)
    print(f">>> [Step 1] Mock SRT created at: {srt_path}")

    # 3. 初始化基础设施
    print(">>> [Step 2] Init Infra (Processor V2)...")
    processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=True,
        debug_dir=work_dir / "debug_logs"
    )

    calculator = CostCalculator(
        pricing_data=settings.GEMINI_PRICING,
        usd_to_rmb_rate=settings.USD_TO_RMB_EXCHANGE_RATE
    )

    # 4. 初始化业务服务
    print(">>> [Step 3] Init Service (CharacterPreAnnotator)...")
    service = CharacterPreAnnotatorService(
        logger=logger,
        gemini_processor=processor,
        cost_calculator=calculator
    )

    # 5. 构造 Payload
    # 注意：我们故意只给部分已知角色，测试 AI 的推理能力
    # 同时测试 "车星星" -> "车小小" 的归一化能力
    payload = {
        "subtitle_path": str(srt_path),
        "known_characters": ["楚昊轩", "车小小","宋安娜"],
        "video_title": "总裁的契约女友",
        "lang": "zh",
        "model_name": "gemini-2.5-flash"
    }

    # 6. 执行
    print(">>> [Step 4] Executing Pipeline...")
    try:
        result = service.execute(payload)

        print("\n" + "=" * 40)
        print("✅ 测试成功! 结果摘要:")
        print("=" * 40)

        # 1. 打印角色分析
        roster = result.get("character_roster", [])
        print(f"\n👥 角色活跃度分析 ({len(roster)}人):")
        for char in roster:
            # 兼容 Pydantic 对象或 Dict
            c_dict = char.model_dump() if hasattr(char, 'model_dump') else char
            print(f"   - {c_dict['name']} (Variations: {c_dict['variations']})")
            print(f"     Lines: {c_dict['stats']['lines']}, Weight: {c_dict['weight_percent']}")

        # 2. 打印字幕流 (抽样)
        subs = result.get("optimized_subtitles", [])
        print(f"\n📜 字幕流抽样 (前5句):")
        for item in subs[:5]:
            i_dict = item.model_dump() if hasattr(item, 'model_dump') else item
            print(f"   [{i_dict['index']}] {i_dict['speaker']}: {i_dict['content']}")

        # 3. 打印成本
        usage = result.get("usage_report", {})
        print(f"\n💰 成本报告:")
        print(f"   Total Tokens: {usage.get('total_tokens')}")
        print(f"   Cost: ${usage.get('cost_usd', 0):.6f} (¥{usage.get('cost_rmb', 0):.4f})")

        # 4. 验证 ASS 生成
        ass_path = result.get("output_ass_path")
        if ass_path:
            print(f"\n📁 ASS 文件生成: {ass_path}")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test()