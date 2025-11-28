# tests/ai_services/dubbing/test_gemini_tts_optimization.py

import os
import json
from pathlib import Path
from google.cloud import texttospeech

# 确保你已经设置了 GOOGLE_APPLICATION_CREDENTIALS 环境变量
# 或者在这里显式设置 (不推荐提交到git):
# os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "path/to/your/key.json"

#PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
PROJECT_ID = 'storygraph-465918'

# --- 1. 准备测试数据 (来自你的 Narration Generator 输出) ---
NARRATION_DATA = {
    "generation_date": "2025-11-28T01:46:10.730579",
    "asset_name": "总裁的契约女友",
    "narration_script": [
        {
            "narration": "哈喽啊，我的互联网怨种兄弟姐妹们！小小我今天就来唠唠，我是怎么从打工人，一步步作死成总裁契约女友的。开头很老套，帮闺蜜替身相亲。结果刚进酒店，就被人当成狐狸精薅头发，简直水逆他妈给水逆开门！为了搞砸相亲，我灵机一动，跟那总裁说，本小姐鱼塘大得很，想娶我就得接受头顶一片草原。",
            # 策略：这一段是开场，需要极高的能量和夸张的语气
            "style_override": "Speak in a high-energy, exaggerated, and sarcastic YouTuber tone. Start very enthusiastically.",
            "tags_insertion": {
                "水逆开门！": "水逆开门！[sigh]",  # 插入叹气
                "草原。": "草原。[laughing]"  # 插入笑声
            }
        },
        {
            "narration": "我以为这招一出，他不得连夜跑路啊？结果你们猜怎么着？这哥们儿脑回路清奇，居然说要跟我结婚！没错，结！婚！我当时就傻了，跟我闺蜜俩人面面相觑，我演得那么渣，他到底图我啥啊？",
            # 策略：这一段充满了难以置信和震惊
            "style_override": "Speak in a shocked and disbelief tone. Emphasize the surprise.",
            "tags_insertion": {
                "怎么着？": "怎么着？[short pause]",  # 制造悬念
                "结！婚！": "结！[medium pause] 婚！"  # 强调节奏
            }
        },
        {
            "narration": "当然，纸包不住火，我冒牌货的身份很快就暴露了。就在我以为要被我们大老板扫地出门时，这位楚昊轩，又刷新了我的三观。他非但没开除我，还逼我签了个契约女友合同，违约金一百倍！救命，我这是签了卖身契吧？从此，白天是社畜车小小，晚上是总裁的戏精女友“车星星”。",
            "style_override": "Speak in a complaining and desperate tone, but still funny.",
            "tags_insertion": {
                "一百倍！": "一百倍！[scared]",  # 使用形容词标记（慎用，测试效果）
                "救命，": "[sigh] 救命，"
            }
        },
        {
            "narration": "你们是不知道这活儿多难干。上班要躲着他，开会汇报紧张得差点当场去世。下班还得背他那光辉履历，什么哈佛海归、富豪榜新贵……我同事在那哇塞好牛，我心里只有一句：完了，要背的又多了。最要命的是见家长，我顶着假发，对着他爷爷猛夸他，说我对他一见钟情。奥斯卡都欠我一座小金人！",
            "style_override": "Fast-paced complaining, like a rapid-fire rant.",
            "tags_insertion": {}
        },
        {
            "narration": "本来我以为这就是一场交易，演好戏就完事。但人心这玩意儿，真不是铁打的。楚昊轩的爷爷对我特别好，搞得我都有点罪恶感了。后来有一次，我不小心被球砸了，楚昊轩这个万年冰山，居然会默默去给我买药膏。看着他那个别扭又关心的样子，我这心里，咯噔一下。完蛋，好像有点不对劲了。",
            "style_override": "Shift to a softer, slightly confused and emotional tone. Less sarcastic.",
            "tags_insertion": {
                "咯噔一下。": "咯噔一下。[short pause]"
            }
        },
        {
            "narration": "姐妹们，我承认我有点动摇了。这到底是职业操守，还是不小心把心给搭进去了？以前我觉得他长得像始祖鸟，现在……好像也没那么丑了。唉，我这该死的恋爱脑！接下来我该怎么办？是继续演戏还是坦白然后赔付天价违约金？在线等，挺急的！",
            "style_override": "Conflicted and anxious tone, ending with an urgent appeal to the audience.",
            "tags_insertion": {
                "始祖鸟，": "始祖鸟，[laughing]",
                "唉，": "[sigh] 唉，"
            }
        }
    ]
}


def apply_tags(text: str, mapping: dict) -> str:
    """辅助函数：将优化标记插入到文本中"""
    for key, val in mapping.items():
        text = text.replace(key, val)
    return text


def synthesize_gemini_tts(text: str, prompt: str, output_filepath: Path):
    """
    调用 Google Cloud Gemini TTS API
    """
    try:
        client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(text=text, prompt=prompt)

        # 选择音色
        # 这里的 model_name 是关键，必须是支持 prompt 的模型
        # 截至目前，gemini-2.5-pro-tts 是预览版名称，如果失败可尝试 'en-US-Journey-F' 等标准 Journey 音色
        # 但为了测试 Gemini 特性，我们使用特定的配置
        voice = texttospeech.VoiceSelectionParams(
            language_code="cmn-CN",  # 中文
            name="Despina",  # 占位符，Gemini TTS 可能会忽略这个 name，主要看 model_name
            # 注意：实际调用时，如果库版本支持自定义 model，应如下设置。
            # 目前 Python SDK 2.29.0+ 可能尚未完全透出 model_name 字段在 VoiceSelectionParams
            # 如果报错，可能需要使用 custom_voice_params 或等待 SDK 更新
            # 这里我们尝试标准调用，Gemini TTS 通常通过特定的 voice name 触发
            # 或者，如果 SDK 支持 model="gemini-..." 我们就传。
            # 假设官方示例代码有效：
            model_name="gemini-2.5-pro-tts"
        )

        # *修正*: 标准 SDK 中 VoiceSelectionParams 可能还没有 model_name 参数。
        # 如果你的环境 SDK 很新，请取消下面这行的注释：
        # voice.model_name = "gemini-2.5-pro-tts"

        # 为了兼容性，我们使用 Journey 音色作为 fallback，或者寻找支持 Gemini 的确切参数
        # 如果你是内测用户，请确保 project 有权限。

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.1  # 稍微加快一点，符合短视频风格
        )

        print(f"  >> Synthesizing...")
        print(f"  >> Prompt: {prompt}")
        print(f"  >> Text: {text[:30]}...")

        # Perform the request
        # 注意：如果 prompt 参数在你的 SDK 版本报错，说明需要更新 google-cloud-texttospeech
        response = client.synthesize_speech(
            request={
                "input": synthesis_input,
                "voice": voice,
                "audio_config": audio_config
            }
        )

        with open(output_filepath, "wb") as out:
            out.write(response.audio_content)
            print(f"  ✅ Audio content written to: {output_filepath}")

    except Exception as e:
        print(f"  ❌ Error generating TTS: {e}")


def main():
    # 设置输出目录
    project_root = Path(__file__).resolve().parents[3]  # 根据文件位置调整
    # output_dir = project_root / "shared_media" / "tmp" / "tts_test_output"
    output_dir = Path("tts_test_output")  # 简单起见放当前目录或指定目录
    output_dir.mkdir(exist_ok=True)

    print(f"Starting Gemini TTS Test for asset: {NARRATION_DATA['asset_name']}")

    for i, item in enumerate(NARRATION_DATA["narration_script"]):
        original_text = item["narration"]

        # 1. 应用标记 (Tags) 优化文本
        final_text = apply_tags(original_text, item.get("tags_insertion", {}))

        # 2. 获取风格提示 (Style Prompt)
        # 如果没有特定的 override，使用一个通用的提示
        style_prompt = item.get("style_override", "Speak in a natural, expressive tone.")

        output_file = output_dir / f"narration_{i:03d}.mp3"

        print(f"\n--- Processing Clip {i + 1} ---")
        synthesize_gemini_tts(final_text, style_prompt, output_file)


if __name__ == "__main__":
    main()