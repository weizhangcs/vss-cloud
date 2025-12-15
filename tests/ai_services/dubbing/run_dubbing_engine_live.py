# tests/ai_services/dubbing/run_dubbing_engine_live.py
# 描述: [Live Debug] Dubbing Engine 实时调试脚本
#       用于排查长句切分后 API 调用是否完整，以及音频拼接是否正常。

import sys
import json
import time
import logging
from pathlib import Path
import yaml

# 1. 路径引导
# 假设文件在 tests/ai_services/dubbing/ 下
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from tests.lib.bootstrap import bootstrap_local_env_and_logger
from ai_services.biz_services.dubbing.dubbing_engine import DubbingEngine
from ai_services.ai_platform.tts.strategies.aliyun_paieas_strategy import AliyunPAIEASStrategy


def main():
    # 2. 引导环境
    settings, logger = bootstrap_local_env_and_logger(project_root)

    # 强制设置 logger 级别为 INFO，甚至 DEBUG
    logger.setLevel(logging.INFO)

    # 3. 准备资源
    # 输入文件：您刚才上传的 narration_script.txt (其实是 JSON)
    # 请确保该文件存在于 tests/testdata/ 或其他位置
    # 这里假设我们将其保存为临时测试文件
    test_input_path = project_root / "tests" / "testdata" / "narration_script_debug.json"

    # 构造一个包含超长句的测试数据 (取自您的 narration_script.txt 中的长段落)
    # 为了方便，我们手动写入这个文件，确保它是我们要测的那个 case
    long_narration_text = (
        "今夜月黑风高，正是偷鸡摸狗的好时候。"
        "黑暗中两个颤巍巍的身影正在操控着不怎么熟练的动作在那青瓦房檐上爬着。"
        "“小姐..咱们下去吧，这也太高了。”"
        "沈醉容正趴在房顶上，闻声后回头朝剪秋做了个噤声的手势。“嘘--剪秋你不要怕,等我们逮住了二哥在青楼里,爹爹知道了肯定要狠狠揍他一顿。”"
        "沈醉容一双狐狸眼里闪着精明的光芒，小手握拳作义愤填膺状，心里想的都是前几日沈竹白捉弄她的事。"
        "剪秋见劝说不动，只好轻叹了口气，又慢吞吞的在房顶上挪动了一些。"
        "沈醉容见她实在害怕，便拍了拍手站起身来，想要过去帮她一把。"
        "剪秋见状后忙张口叮嘱道:\"小姐你小心点。\""
        "沈醉容一脸不怕死的样子，大手一挥，十分心大的说道:“没事不用担心我，来剪秋，你站起来，我扶着..\""
        "话还没说完，沈醉容突然感觉到脚底下的踩着的瓦片有些松动，还不待自己反应过来发生了什么，整个人突然就失重般朝下面掉了下去。"
        "“小姐!\""
        "沈醉容一声惊呼卡在喉咙间还未出口，一阵天旋地转后就摔在了一片柔软上,这时她才后知后觉的“哎呦”了一声。"
        "嗯?好像不痛!"
        "沈醉容正庆幸的想着自己正好摔到了床上，鼻息间突然传来一股香气，像是胭脂俗粉的味道，她吸了吸鼻子，一个没忍住,打了个喷嚏。"
        "“阿嚏!!\""
        "等沈醉容揉了揉鼻子，还没有舒服上一会儿，就感觉到一股无形的压力笼罩在了自己周围她抬眼一看，透过半掩着的红色床纱的间隙,冷不丁的对上了一双眼尾上翘着的凤眼，正目光冰冷的看着她。"
        "再往旁边一扫，那里坐着一个面若桃花般的美人,好像是被她吓到了,一双美目里还尽是惊异。"
        "见此状况，沈醉容心里了然。"
        "害，不小心打扰人好事了，正准备开口赔个不是时时.…. "
        "等等!这人长的怎么那么像摄政王??"
        "坐在房中央的傅郁之看着突然从天而降的人摔趴在一片碎瓦的床上，一双带着些许魅意的狐狸眼看到他时瞪了瞪，像是难以置信似的,又眯了眯，白皙稚嫩的脸上这才显露出慌乱。"
        "沈醉容反应过来自己捣了谁的窝之后突然结巴了起来,当朝摄政王啊!傅郁之啊!自家老爹也惹不起的人物，自己怎么就好死不死摔到了这里!!"
        "沈醉容一边痛心疾首的在心里把那个晦气的二哥骂了千八百遍，一边在脑子里飞速想着该如何脱身。"
        "看着傅郁之冷冷盯着她的样子，沈醉容眼睛滴溜溜一转，自来熟般的换上一副甜甜的笑脸想要跟人套套近乎，哪料脑子一抽问道:“摄政王您也来逛窑子啊??"
        "闻言后只见傅郁之的嘴角抽动了一下，似乎是不知道该怎么回她，沈醉容反应过来也是恨不得抽自己两大嘴巴。"
        "手下紧紧的攥着床上的被褥，正揣揣不安的等待着，坐在傅郁之旁边一直打量着她的那位女子突然开口了。"
        "“你是沈丞相家的千金吧?怎么会从房顶上掉下来 ?"
        "沈醉容顶着头顶的威压磨磨蹭蹭的从床上爬了下来，随即开口解释道:“我是...我走夜路,一不小心从上面掉下来了。"
    )
    mock_input_data = {
        "narration_script": [
            {
                "narration": long_narration_text,
                "source_scene_ids": [10, 11]
            }
        ],
        "config_snapshot": {
            #"control_params": {"style": "emotional"}
        }
    }

    test_input_path.parent.mkdir(parents=True, exist_ok=True)
    with test_input_path.open("w", encoding="utf-8") as f:
        json.dump(mock_input_data, f, indent=2, ensure_ascii=False)

    # 输出目录
    work_dir = project_root / "shared_media" / "tmp" / "dubbing_live_debug"
    if work_dir.exists():
        import shutil
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 4. 组装依赖
    logger.info("正在组装 DubbingEngine...")

    # 4.1 策略
    if not settings.PAI_EAS_SERVICE_URL:
        logger.error("❌ 错误: .env 中未配置 PAI_EAS_SERVICE_URL，无法进行 Live 测试。")
        return

    strategy_paieas = AliyunPAIEASStrategy(
        service_url=settings.PAI_EAS_SERVICE_URL,
        token=settings.PAI_EAS_TOKEN
    )
    strategies = {"aliyun_paieas": strategy_paieas}

    # 4.2 模板 (Mock 一个使用 replication 的模板)
    # 注意：这里我们需要一个真实的参考音频路径
    ref_audio_rel_path = "ai_services/biz_services/dubbing/reference/zero_shot_prompt.wav"
    ref_audio_abs_path = project_root / "shared_media" / ref_audio_rel_path

    if not ref_audio_abs_path.exists():
        logger.warning(f"⚠️ 参考音频不存在: {ref_audio_abs_path}。将创建一个假的空文件以绕过检查 (实际调用会失败)")
        ref_audio_abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ref_audio_abs_path, 'wb') as f: f.write(b'RIFF....WAVEfmt ....data....')

    templates = {
        "debug_template": {
            "provider": "aliyun_paieas",
            "method": "replication",
            "audio_format": "wav",  # 使用 wav 方便调试
            "replication_source": {
                "audio_path": ref_audio_rel_path,
                "text": "希望你以后能够做得比我还好哟"
            },
            "params": {"speed": 1.0}
        }
    }

    metadata_dir = project_root / "ai_services" / "dubbing" / "metadata"

    # 5. 实例化引擎
    engine = DubbingEngine(
        logger=logger,
        work_dir=work_dir,
        strategies=strategies,
        templates=templates,
        metadata_dir=metadata_dir,
        shared_root_path=project_root / "shared_media"
    )

    # 6. 执行 (Live)
    logger.info(">>> 开始执行 Live Dubbing 测试...")

    try:
        start_time = time.time()

        # 我们显式传入 style='humorous' 看看 instruct 是否生效
        result = engine.execute(
            narration_path=test_input_path,
            template_name="debug_template",
            style="humorous",
            lang="zh"
        )

        duration = time.time() - start_time
        logger.info(f"✅ 执行完成 (耗时 {duration:.2f}s)")

        # 7. 验证结果
        script = result.get("dubbing_script", [])
        for item in script:
            audio_path = item.get("audio_file_path")
            print(f"\n--- Result ---")
            print(f"Text: {item.get('narration')[:50]}...")
            if item.get("error"):
                print(f"❌ Error: {item.get('error')}")
            else:
                print(f"✅ Audio: {audio_path}")
                print(f"   Duration: {item.get('duration_seconds')}s")

                # 检查物理文件
                abs_path = project_root / "shared_media" / audio_path
                if abs_path.exists():
                    print(f"   File Size: {abs_path.stat().st_size} bytes")
                else:
                    print(f"   ❌ File Not Found on Disk!")

    except Exception as e:
        logger.error(f"❌ 测试崩溃: {e}", exc_info=True)


if __name__ == "__main__":
    main()