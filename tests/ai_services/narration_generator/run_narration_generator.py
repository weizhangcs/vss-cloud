# tests/run_narration_generator.py
# 描述: [终极集成测试] 验证 Narration Generator 的全链路编排能力
#       包含 10+ 个覆盖各种边缘情况和参数组合的测试用例。
#
# 用法:
#   1. 运行所有测试: python tests/run_narration_generator.py
#   2. 运行特定测试: python tests/run_narration_generator.py --case Case_A_Deep_Emotion
#   3. 列出所有测试: python tests/run_narration_generator.py --list

import sys
import json
import time
import argparse
from pathlib import Path

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

# 导入引导程序
from tests.lib.bootstrap import bootstrap_local_env_and_logger

# 导入依赖组件
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.narration.narration_generator import NarrationGenerator

# ==============================================================================
# 测试用例定义 (10个典型场景)
# ==============================================================================
TEST_CASES = [
    {
        "name": "Case_A_Deep_Emotion",
        "desc": "【深情线】聚焦车小小和楚昊轩的前5集感情发展，深情电台风",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 30,
            "control_params": {
                "narrative_focus": "romantic_progression",
                "scope": {"type": "episode_range", "value": [1, 5]},
                "character_focus": {"mode": "specific", "characters": ["车小小", "楚昊轩"]},
                "style": "emotional",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_B_Suspense_Reveal",
        "desc": "【悬疑线】全剧范围，聚焦身份揭秘与反转，悬疑解密风",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 60,
            "control_params": {
                "narrative_focus": "suspense_reveal",
                "scope": {"type": "full"},
                "style": "suspense",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_C_Humorous_Roast",
        "desc": "【毒舌线】全剧高光时刻，幽默吐槽风 (时长压力测试)",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 50,
            "control_params": {
                "narrative_focus": "general",
                "scope": {"type": "full"},
                "style": "humorous",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_D_First_Person_POV",
        "desc": "【第一人称】车小小自述，沉浸式人物志",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 40,
            "control_params": {
                "narrative_focus": "character_growth",
                "scope": {"type": "full"},
                "character_focus": {"mode": "specific", "characters": ["车小小"]},
                "style": "emotional",
                "perspective": "first_person",
                "perspective_character": "车小小"
            }
        }
    },
    {
        "name": "Case_E_Short_Video",
        "desc": "【短视频速看】严格限制1分钟，测试时长控制与精简能力",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 20,
            "speaking_rate": 4.5,  # 稍微调快语速
            "control_params": {
                "narrative_focus": "general",
                "scope": {"type": "episode_range", "value": [1, 3]},
                "style": "objective",
                "target_duration_minutes": 1  # 强约束
            }
        }
    },
    {
        "name": "Case_F_Business_Arc",
        "desc": "【搞事业线】聚焦职场冲突与商业复仇，严肃风格",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 40,
            "control_params": {
                "narrative_focus": "business_success",
                "scope": {"type": "full"},
                "character_focus": {"mode": "specific", "characters": ["楚昊轩"]},
                "style": "objective",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_G_Antagonist_Perspective",
        "desc": "【反派视角】聚焦女配角宋安娜的心理活动",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 30,
            "control_params": {
                "narrative_focus": "general",
                "scope": {"type": "full"},
                "character_focus": {"mode": "specific", "characters": ["宋安娜"]},
                "style": "emotional",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_H_Mid_Season_Recap",
        "desc": "【中段剧情回顾】只关注第10-20集，测试范围过滤的准确性",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 40,
            "control_params": {
                "narrative_focus": "general",
                "scope": {"type": "episode_range", "value": [10, 20]},
                "style": "objective"
            }
        }
    },
    {
        "name": "Case_I_English_Narration",
        "desc": "【英文解说】测试 i18n 支持 (输出英文脚本)",
        "config": {
            "lang": "en",  # 切换语言
            "model": "gemini-2.5-flash",
            "rag_top_k": 40,
            "control_params": {
                "narrative_focus": "romantic_progression",
                "scope": {"type": "episode_range", "value": [1, 5]},
                "style": "emotional",
                "perspective": "third_person"
            }
        }
    },
    {
        "name": "Case_J_Long_Summary",
        "desc": "【长篇深度解说】目标5分钟，全剧深度解析",
        "config": {
            "lang": "zh",
            "model": "gemini-2.5-flash",
            "rag_top_k": 80,  # 检索更多上下文
            "control_params": {
                "narrative_focus": "general",
                "scope": {"type": "full"},
                "style": "objective",
                "target_duration_minutes": 5
            }
        }
    }
]


def main():
    # 1. 解析命令行参数
    parser = argparse.ArgumentParser(description="Narration Generator 集成测试套件")
    parser.add_argument("--case", type=str, help="指定运行的测试用例名称 (e.g., Case_A_Deep_Emotion)")
    parser.add_argument("--list", action="store_true", help="列出所有可用测试用例并退出")
    args = parser.parse_args()

    # 列出模式
    if args.list:
        print("\n📋 可用测试用例列表:")
        for case in TEST_CASES:
            print(f"  - {case['name']:<30} : {case['desc']}")
        return

    # 2. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 3. 定义资源路径
    blueprint_path = project_root / "tests" / "testdata" / "narrative_blueprint_28099a52_KRe4vd0.json"
    narration_base = project_root / "ai_services" / "narration"
    prompts_dir = narration_base / "prompts"
    metadata_dir = narration_base / "metadata"
    rag_schema_path = project_root / "ai_services" / "ai_platform" / "rag" / "metadata" / "schemas.json"

    # [修改] 输出目录归整到 shared_media/outputs/
    output_dir = project_root / "shared_media" / "outputs" / "narration_v2_test_result"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4. 初始化服务
    logger.info("正在初始化 GeminiProcessor...")
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        # [修改] 调试日志指向 shared_media/logs
        debug_dir=project_root / "shared_media" / "logs" / "narration_v2_debug"
    )

    logger.info("正在初始化 NarrationGenerator...")
    generator = NarrationGenerator(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        prompts_dir=prompts_dir,
        metadata_dir=metadata_dir,
        rag_schema_path=rag_schema_path,
        logger=logger,
        work_dir=output_dir / "workspace",
        gemini_processor=gemini_processor
    )

    # 5. 筛选要运行的测试用例
    cases_to_run = []
    if args.case:
        found = next((c for c in TEST_CASES if c["name"] == args.case), None)
        if not found:
            logger.error(f"未找到名称为 '{args.case}' 的测试用例。请使用 --list 查看可用列表。")
            sys.exit(1)
        cases_to_run = [found]
    else:
        cases_to_run = TEST_CASES

    # 6. 执行测试循环
    RAG_CORPUS_NAME = "20251104-Test"
    SERIES_NAME = "总裁的契约女友"

    logger.info(f"准备执行 {len(cases_to_run)} 个测试用例...")

    for case in cases_to_run:
        print("\n" + "=" * 70)
        logger.info(f"🚀 [Running] {case['name']}")
        logger.info(f"ℹ️  Description: {case['desc']}")
        print("=" * 70)

        try:
            start_time = time.time()

            result = generator.execute(
                series_name=SERIES_NAME,
                corpus_display_name=RAG_CORPUS_NAME,
                blueprint_path=blueprint_path,
                config=case['config']
            )

            duration = time.time() - start_time
            script = result.get("narration_script", [])

            # 统计 Refine 情况
            refined_count = sum(1 for s in script if s.get("metadata", {}).get("refined"))

            logger.info(f"✅ 执行成功 (耗时: {duration:.2f}s)")
            logger.info(f"📊 产出统计: {len(script)} 段解说 | {refined_count} 段触发了缩写优化")

            print("\n--- 📝 预览 (首段) ---")
            if script:
                first = script[0]
                print(f"Text: {first.get('narration')[:100]}...")
                print(f"Source: {first.get('source_scene_ids')}")
            else:
                print("(无内容生成)")

            # 保存结果
            save_path = output_dir / f"result_{case['name']}.json"
            with save_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"💾 结果已保存: {save_path.name}")

        except Exception as e:
            logger.error(f"❌ 用例 {case['name']} 执行失败: {e}", exc_info=True)

        # 稍微停顿，避免 API Rate Limit
        time.sleep(1)

    print("\n✨ 所有计划测试已完成。")


if __name__ == "__main__":
    main()