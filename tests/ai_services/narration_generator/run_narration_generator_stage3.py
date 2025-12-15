# tests/run_narration_generator_v2_stage3.py
# 描述: [Stage 3] 完整链路验证 - 从配置到最终解说词生成
# 运行方式: python tests/run_narration_generator_v2_stage3.py

import sys
import json
import logging
from pathlib import Path

# 引入项目路径
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from tests.lib.bootstrap import bootstrap_local_env_and_logger
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.rag.schemas import load_i18n_strings


# 复用之前验证过的类 (为了演示完整性，这里简化引入或直接定义)
# 在正式工程中，这些应该已经被重构到了 ai_services/narration/ 目录下的正式文件中
# 这里我们模拟 Stage 2 的输出结果，直接测试 Stage 3 的 Prompt 组装和推理

class NarrationSynthesizer:
    """
    [Stage 3 核心逻辑]
    负责组装最终 Prompt (System + User) 并调用 LLM。
    """

    # 风格定义 (未来可移至配置文件)
    STYLES = {
        "objective": "你是一位客观冷静的纪录片解说员，语调平实，注重事实陈述。",
        "humorous": "你是一位幽默风趣的短视频博主，喜欢用犀利的语言吐槽剧情槽点，语言通俗活泼。",
        "emotional": "你是一位情感细腻的电台主播，善于捕捉人物内心的波澜，用感性的语言渲染氛围。",
        "suspense": "你是一位悬疑故事讲述者，善于制造悬念，层层剥茧，引导观众探寻真相。"
    }

    def __init__(self, gemini_processor: GeminiProcessor, prompts_dir: Path, logger: logging.Logger):
        self.processor = gemini_processor
        self.prompts_dir = prompts_dir
        self.logger = logger

    def _load_base_prompt(self, lang: str = "zh") -> str:
        # 加载基础 Prompt 模版 (narration_generator_zh.txt)
        # 这里假设文件存在
        path = self.prompts_dir / f"narration_generator_{lang}.txt"
        return path.read_text(encoding="utf-8")

    def generate(self, context: str, config: dict, series_name: str) -> dict:
        control = config.get("control_params", {})
        style_key = control.get("style", "objective")
        style_instruction = self.STYLES.get(style_key, self.STYLES["objective"])

        self.logger.info(f"采用解说风格: {style_key}")

        # 1. 加载基础模版
        base_template = self._load_base_prompt("zh")

        # 2. 注入上下文 (rag_context)
        # 注意：我们在 Prompt 模版中预留了 {rag_context} 占位符
        user_prompt = base_template.format(rag_context=context)

        # 3. 动态构建 System Instruction (用于控制风格)
        # 强调：这里不仅设定角色，还明确要求覆盖默认的“客观中立”规则
        system_instruction = f"""
        【重要指令：角色与风格设定】
        你现在的身份是：{style_instruction}
        请忽略基础模版中关于“客观中立”的常规要求，必须严格按照上述身份的语气和口吻来生成解说词。
        """

        # --- [修复点]：显式拼接 Prompt ---
        full_prompt = system_instruction + "\n\n" + user_prompt
        # -------------------------------

        self.logger.info("正在调用 Gemini 进行推理...")

        # 4. 调用 Gemini
        response_data, usage = self.processor.generate_content(
            model_name=config.get("model", "gemini-2.5-flash"),  # 支持从 config 读取模型
            prompt=full_prompt,  # <--- 这里传入拼接后的完整 Prompt
            temperature=0.7  # [建议] 稍微调高温度，让风格化发挥得更淋漓尽致
            # 注意：目前 GeminiProcessor 的同步方法暂时没有显式接收 system_instruction 参数
            # 我们通常将其拼接到 prompt 最前面，或者修改 Processor 支持 system_instruction
            # 这里采用拼接方式：
            # prompt = system_instruction + "\n\n" + user_prompt
            # 但为了保持原 prompt 结构的纯净，我们假设 base_template 已经包含了必要的指令结构
            # 更好的做法是修改 GeminiProcessor 支持 system_instruction，但在 VSS Cloud 现状下，
            # 我们把 style_instruction 注入到 user_prompt 的头部更稳妥。
        )

        return response_data, usage


def main():
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 1. 准备依赖
    # 加载 RAG 语言包 (Stage 2 依赖)
    rag_schema_path = project_root / "ai_services" / "ai_platform" / "rag" / "metadata" / "schemas.json"

    gemini_processor = GeminiProcessor(
        api_key=settings.GOOGLE_API_KEY,
        logger=logger,
        debug_mode=True,
        debug_dir=project_root / "shared_media" / "logs" / "narration_debug"
    )

    prompts_dir = project_root / "ai_services" / "narration" / "prompts"

    # 2. 模拟 Stage 2 产出的 Context (直接使用您刚才验证通过的文本内容)
    # 为了测试方便，我们手动构造一个经过 Stage 2 清洗后的 Context
    # 真实场景下，这里是通过 Enhancer.enhance() 产出的
    mock_enhanced_context = """
--- Metadata Block ---
剧集ID: 总裁的契约女友_v3
场景ID: 10
地点: 在咖啡店里
氛围: Tense
出场角色: 楚昊轩, 车小小
本场景的核心叙事是: 车小小决定为了姐妹继续跟楚昊轩相亲... (略)
--- Dialogues ---
- 车小小: 为了姐妹 雄起
- 楚昊轩: 你不冷吗
(这里是清洗后的高质量文本...)
    """

    # 3. 定义测试配置 (测试 "幽默吐槽" 风格)
    test_config = {
        "model": "gemini-2.5-flash",  # 使用 .env 中配置的新模型
        "control_params": {
            "style": "humorous",
            "target_duration": "short"
        }
    }

    # 4. 执行 Stage 3
    synthesizer = NarrationSynthesizer(gemini_processor, prompts_dir, logger)

    try:
        result, usage = synthesizer.generate(mock_enhanced_context, test_config, "总裁的契约女友")

        print("\n" + "=" * 20 + " 生成结果 " + "=" * 20)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\n" + "=" * 20 + " 用量统计 " + "=" * 20)
        print(usage)

    except Exception as e:
        logger.error(f"推理失败: {e}", exc_info=True)


if __name__ == "__main__":
    main()