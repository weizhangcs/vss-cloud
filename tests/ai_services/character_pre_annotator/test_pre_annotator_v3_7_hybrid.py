# tests/ai_services/character_pre_annotator/test_pre_annotator_v3_7_hybrid.py

import sys
import json
import logging
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

# ==============================================================================
# 0. 环境路径设置
# ==============================================================================
# 定位到项目根目录
current_file = Path(__file__).resolve()
project_root = current_file.parents[3]
sys.path.append(str(project_root))

# 导入 Django settings 引用
from django.conf import settings as django_settings

from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from ai_services.biz_services.character_pre_annotator.service import CharacterPreAnnotatorService
from tests.lib.bootstrap import bootstrap_local_env_and_logger


def create_mock_srt(work_dir: Path) -> Path:
    """创建一个简单的测试用 SRT 文件"""
    content = """1
00:00:01,000 --> 00:00:04,000
楚昊轩，你怎么来了？

2
00:00:04,500 --> 00:00:07,000
我是来看看我的契约女友车小小的。

3
00:00:08,000 --> 00:00:10,000
哼，少来这套，明明是你自己想吃甜品了。
"""
    srt_path = work_dir / "test_dialogue.srt"
    srt_path.write_text(content, encoding='utf-8')
    return srt_path


def run_test():
    # 1. 引导环境 (加载 .env)
    # bootstrap 返回的 settings 是一个 SimpleNamespace 模拟对象，不是真正的 django.conf.settings
    env_config, logger = bootstrap_local_env_and_logger(project_root)

    if not env_config.GOOGLE_API_KEY:
        print("❌ 错误: 未找到 GOOGLE_API_KEY，无法进行真实推理测试")
        return

    # 准备工作目录 (本地)
    local_shared_root = project_root / "shared_media"
    local_tmp_root = local_shared_root / "tmp"
    work_dir = local_tmp_root / "char_pre_v3_7_test"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2. 准备数据
    srt_path = create_mock_srt(work_dir)
    abs_srt_path = srt_path.resolve()

    print(f">>> [Step 1] Mock SRT created at: {abs_srt_path}")

    # 3. 初始化基础设施
    print(">>> [Step 2] Init Infra (Processor V2)...")
    processor = GeminiProcessor(
        api_key=env_config.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=True,
        debug_dir=work_dir / "debug_logs"
    )

    calculator = CostCalculator(
        pricing_data=env_config.GEMINI_PRICING,
        usd_to_rmb_rate=env_config.USD_TO_RMB_EXCHANGE_RATE
    )

    # 4. 初始化业务服务
    print(">>> [Step 3] Init Service (CharacterPreAnnotatorService)...")
    service = CharacterPreAnnotatorService(
        logger=logger,
        gemini_processor=processor,
        cost_calculator=calculator
    )

    # 5. 构造 Payload
    # [变更] 模拟客户端行为，传入相对路径 "tmp/char_pre_v3_7_test/test_dialogue.srt"
    # 我们假设 work_dir 是在 SHARED_ROOT/tmp 下创建的

    # 计算相对路径: work_dir 相对于 shared_media 的路径
    # 例如: tmp/char_pre_v3_7_test
    try:
        # 在 patch 环境下，我们需要相对于我们 mock 的 shared_root 计算
        # 本地测试时: D:\...\shared_media
        local_shared_root = project_root / "shared_media"
        relative_srt_path = srt_path.relative_to(local_shared_root)
    except ValueError:
        # Fallback (仅防万一)
        relative_srt_path = "tmp/char_pre_v3_7_test/test_dialogue.srt"

    print(f"Testing with Relative Path: {relative_srt_path}")
    payload = {
        "subtitle_path": str(relative_srt_path),
        "known_characters": ["楚昊轩", "车小小"],
        "video_title": "总裁的契约女友测试片段",
        "lang": "zh",
        "model_name": "gemini-2.5-flash",
        "batch_size": 10,
        "temperature": 0.1
    }

    # [模拟 Handler 逻辑]
    service_payload = payload.copy()
    if not payload['subtitle_path'].startswith("gs://"):
        # 模拟 Handler 将相对路径转为绝对路径
        # 注意：这里用的是我们 patch 进去的 local_shared_root
        resolved_path = local_shared_root / payload['subtitle_path']
        service_payload['subtitle_path'] = str(resolved_path)
        print(f"[Handler Mock] Resolved path: {resolved_path}")

    # 6. 执行 Service (传入处理后的 payload)
    print(f">>> [Step 4] Executing Pipeline with Payload...")

    try:
        result = service.execute(payload)

        print("\n" + "=" * 40)
        print("✅ 测试成功 (V3.7 Hybrid Mode)! 结果摘要:")
        print("=" * 40)

        # 1. 验证输出文件路径
        output_ass_rel = result.get("output_ass_path")
        # 拼接回绝对路径进行检查
        output_ass_abs = local_shared_root / output_ass_rel if output_ass_rel else None

        print(f"\n📁 生成的 ASS 文件(Rel): {output_ass_rel}")
        print(f"   -> 检查路径: {output_ass_abs}")

        if output_ass_abs and output_ass_abs.exists():
            print("   -> ✅ 文件物理存在验证通过！")
        else:
            print("   -> ❌ 警告: 文件依然未找到！请检查是否写入到了 D:\\app\\...？")

        # 2. 打印字幕流
        subs = result.get("optimized_subtitles", [])
        print(f"\n📜 推理结果 ({len(subs)} lines):")
        for item in subs:
            i_dict = item.model_dump() if hasattr(item, 'model_dump') else item
            print(f"   [{i_dict['index']}] {i_dict['speaker']}: {i_dict['content']}")

        # 3. 打印角色
        roster = result.get("character_roster", [])
        print(f"\n👥 角色识别结果: {[r['name'] for r in roster]}")

        # 4. 打印成本
        usage = result.get("usage_report", {})
        print(f"\n💰 成本报告: ${usage.get('cost_usd', 0):.6f}")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test()