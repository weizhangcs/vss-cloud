# tests/run_narration_generator_v2.py
# 描述: [终极集成测试] 验证 Narration Generator V2 的全链路编排能力
#       测试多种参数组合（范围、角色、焦点、风格）对生成结果的影响。
# 运行方式: python tests/run_narration_generator_v2.py

import sys
import json
import time
from pathlib import Path

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

# 导入引导程序
from utils.local_execution_bootstrap import bootstrap_local_env_and_logger

# 导入依赖组件
from ai_services.common.gemini.gemini_processor import GeminiProcessor
from ai_services.narration.narration_generator_v2 import NarrationGeneratorV2


def main():
    # 1. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 2. 定义路径资源
    # [输入] 本地蓝图文件 (Stage 2 必需)
    blueprint_path = project_root / "shared_media" / "resources" / "tests" / "testdata" / "narrative_blueprint_28099a52_KRe4vd0.json"

    # [配置] 服务所需的元数据目录
    base_narration_dir = project_root / "ai_services" / "narration"
    prompts_dir = base_narration_dir / "prompts"
    metadata_dir = base_narration_dir / "metadata"
    rag_schema_path = project_root / "ai_services" / "rag" / "metadata" / "schemas.json"

    # [输出] 测试结果保存目录
    output_dir = project_root / "shared_media" / "resources" / "tests" / "local_test_result" / "narration_v2"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 3. 初始化依赖服务
    logger.info("正在初始化 GeminiProcessor...")
    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=settings.DEBUG,
        debug_dir=output_dir / "debug_logs"
    )

    logger.info("正在初始化 NarrationGeneratorV2...")
    generator = NarrationGeneratorV2(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        prompts_dir=prompts_dir,
        metadata_dir=metadata_dir,
        rag_schema_path=rag_schema_path,
        logger=logger,
        work_dir=output_dir / "workspace",
        gemini_processor=gemini_processor
    )

    # 4. 定义多样化的测试用例 (Mock 参数组合)
    # 注意：corpus_display_name 请替换为您 RAG 中真实的语料库名称 (例如 '20251104-Test')
    RAG_CORPUS_NAME = "20251104-Test"
    SERIES_NAME = "总裁的契约女友"

    test_cases = [
        {
            "name": "Case_A_Deep_Emotion",
            "desc": "【深情线】聚焦车小小和楚昊轩的前5集感情发展，深情电台风，上帝视角",
            "config": {
                "lang": "zh",
                "model": "gemini-2.5-flash",
                "rag_top_k": 20,
                "control_params": {
                    "narrative_focus": "romantic_progression",
                    "scope": {
                        "type": "episode_range",
                        "value": [1, 5]
                    },
                    "character_focus": {
                        "mode": "specific",
                        "characters": ["车小小", "楚昊轩"]
                    },
                    "style": "emotional",
                    # [新增] 显式指定第三人称
                    "perspective": "third_person"
                }
            }
        },
        {
            "name": "Case_B_Suspense_Reveal",
            "desc": "【悬疑线】聚焦全剧冲突与反转，悬疑解密风，上帝视角",
            "config": {
                "lang": "zh",
                "model": "gemini-2.5-flash",
                "rag_top_k": 50,
                "control_params": {
                    "narrative_focus": "suspense_reveal",
                    "scope": {
                        "type": "episode_range",
                        "value": [1, 30]
                    },
                    "style": "suspense",
                    # [新增] 显式指定第三人称
                    "perspective": "third_person"
                }
            }
        },
        {
            "name": "Case_C_Humorous_Roast",
            "desc": "【毒舌线】全剧高光时刻，幽默吐槽风，上帝视角",
            "config": {
                "lang": "zh",
                "model": "gemini-2.5-flash",
                "rag_top_k": 30,
                "control_params": {
                    "narrative_focus": "general",
                    "scope": {
                        "type": "full"
                    },
                    "style": "humorous",
                    # [新增] 显式指定第三人称
                    "perspective": "third_person"
                }
            }
        },
        {
            "name": "Case_D_First_Person_POV",
            "desc": "【第一人称】车小小自述，体验角色沉浸感 (验证变量替换)",
            "config": {
                "lang": "zh",
                "model": "gemini-2.5-flash",
                "rag_top_k": 30,
                "control_params": {
                    "narrative_focus": "character_growth",  # 关注个人成长
                    "scope": {
                        "type": "full"
                    },
                    "character_focus": {
                        "mode": "specific",
                        "characters": ["车小小"]
                    },
                    "style": "emotional",  # 深情自述
                    # [新增] 测试第一人称逻辑
                    "perspective": "first_person",
                    "perspective_character": "车小小"  # 必须替换 Prompt 中的 {character}
                }
            }
        }
    ]

    # 5. 执行循环测试
    for case in test_cases:
        print("\n" + "=" * 60)
        logger.info(f"🚀 执行测试用例: {case['name']} ({case['desc']})")
        print("=" * 60)

        try:
            start_time = time.time()

            result = generator.execute(
                series_name=SERIES_NAME,
                corpus_display_name=RAG_CORPUS_NAME,
                blueprint_path=blueprint_path,
                config=case['config']
            )

            duration = time.time() - start_time

            # 6. 打印结果摘要
            script = result.get("narration_script", [])
            logger.info(f"✅ 生成完成 (耗时: {duration:.2f}s). 包含 {len(script)} 段解说。")

            print("\n--- 📝 解说词预览 (Top 1) ---")
            if script:
                first_entry = script[0]
                print(f"内容: {first_entry.get('narration')}")
                print(f"溯源: Scene IDs {first_entry.get('source_scene_ids')}")
            else:
                print("(无生成内容 - 可能被过滤为空)")

            # 7. 保存独立的结果文件
            save_path = output_dir / f"result_{case['name']}.json"
            with save_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"结果已保存至: {save_path}")

        except Exception as e:
            logger.error(f"❌ 用例 {case['name']} 执行失败: {e}", exc_info=True)

        print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    main()