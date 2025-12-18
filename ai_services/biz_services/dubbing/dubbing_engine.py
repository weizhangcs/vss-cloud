import json
import logging
import yaml
from pathlib import Path
from typing import Dict, Any, List

# Core Units
from ai_services.ai_core_units.audio_director.director import AudioDirector
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.tts.strategies.base_strategy import TTSStrategy

# Biz Models
from ai_services.biz_services.dubbing.schemas import DubbingServiceParams, DubbingResult, DubbingSnippetResult
from ai_services.biz_services.narrative_dataset import NarrativeDataset
from core.exceptions import BizException
from core.error_codes import ErrorCode


class DubbingEngine:
    SERVICE_NAME = "dubbing_engine"

    def __init__(self,
                 logger: logging.Logger,
                 gemini_processor: GeminiProcessor,  # 用于 Director
                 work_dir: Path,  # 音频临时输出目录
                 strategies: Dict[str, TTSStrategy],
                 templates_config_path: Path,
                 director_prompts_dir: Path,
                 shared_root_path: Path):

        self.logger = logger
        self.work_dir = work_dir
        self.strategies = strategies
        self.shared_root_path = shared_root_path

        # 加载 TTS 模板配置
        with templates_config_path.open('r', encoding='utf-8') as f:
            self.templates = yaml.safe_load(f)

        # 初始化导演
        self.director = AudioDirector(gemini_processor, director_prompts_dir)

        self.logger.info("DubbingEngine initialized (Direct-Then-Dub Mode).")

    def execute(self,
                narration_data: Dict[str, Any],  # 源 JSON 数据
                dataset: NarrativeDataset,  # [Context] 以后可用于更精细的控制
                config: DubbingServiceParams) -> Dict[str, Any]:

        # 1. 解析基础配置
        template_name = config.template_name
        template = self.templates.get(template_name)
        if not template:
            raise ValueError(f"Template '{template_name}' not found")

        provider = template.get("provider")
        strategy = self.strategies.get(provider)
        if not strategy:
            raise ValueError(f"Strategy '{provider}' not found")

        # 2. 准备脚本 (Convert to Dict list for processing)
        # narration_data["narration_script"] 是 List[Dict]
        script_list = narration_data.get("narration_script", [])
        if not script_list:
            raise BizException(ErrorCode.INVALID_PARAM, msg="Input script is empty")

        # 3. [Phase 1: Directing] 导演介入
        # 只有 Google TTS 这种高级引擎才需要导演，Aliyun 等通常只需要纯文本
        # 我们通过 provider 类型或者模板配置来判断是否需要 Directing
        # 这里简单逻辑：如果 provider 是 google_tts，则调用导演
        usage_info = {}

        if provider == "google_tts":
            self.logger.info("Provider is Google TTS. Invoking AudioDirector...")
            # Direct logic modifies script_list in-place
            script_list, usage = self.director.direct_script(
                script=script_list,
                lang=config.target_lang,
                model="gemini-2.5-flash",  # 导演用快模型即可
                style=config.style,
                perspective=config.perspective
            )
            usage_info = usage
        else:
            self.logger.info(f"Provider {provider} does not require Directing. Skipping.")

        # 4. [Phase 2: Dubbing] 循环合成
        results = []
        total_duration = 0.0

        base_params = template.get("params", {}).copy()
        ext = template.get('audio_format', 'mp3')

        self.logger.info(f"Starting Synthesis Loop ({len(script_list)} clips)...")

        for idx, entry in enumerate(script_list):
            # 4.1 文本选择逻辑
            if provider == "google_tts":
                # 优先用导演加了 [sigh] 的文本
                text = entry.get("narration_for_audio") or entry.get("narration", "")
                # 注入动态指令
                current_params = base_params.copy()
                if entry.get("tts_instruct"):
                    current_params["instruct"] = entry.get("tts_instruct")
            else:
                # 其他引擎用纯文本
                text = entry.get("narration", "")
                current_params = base_params.copy()

            if not text:
                continue

            # 4.2 执行合成
            final_filename = f"audio_{idx:03d}.{ext}"
            final_path = self.work_dir / final_filename

            try:
                duration = strategy.synthesize(text, final_path, current_params)

                # I/O Guard
                if not final_path.exists() or final_path.stat().st_size < 100:
                    raise BizException(ErrorCode.TTS_GENERATION_ERROR, msg="Zero byte audio file")

                # 计算相对路径 (用于前端下载)
                rel_path = final_path.relative_to(self.shared_root_path)

                # 4.3 构造结果条目
                # 先把原 entry 转为 Schema (DubbingSnippetResult 兼容 NarrationSnippet 字段)
                # 注意：entry 现在可能包含 tts_instruct 等新字段，需要合并
                snippet_res = DubbingSnippetResult(
                    **entry,  # 包含 index, narration, source等
                    audio_file_path=str(rel_path),
                    duration_seconds=round(duration, 2)
                )
                results.append(snippet_res)
                total_duration += duration

                self.logger.info(f"   Generated clip {idx}: {duration}s")

            except Exception as e:
                self.logger.error(f"Clip {idx} failed: {e}")
                raise BizException(ErrorCode.TTS_GENERATION_ERROR, msg=f"Clip {idx} failed: {e}")

        # 5. 最终结果封装
        final_result = DubbingResult(
            generation_date=narration_data.get("generation_date"),
            asset_name=narration_data.get("asset_name"),
            template_name=template_name,
            total_duration=round(total_duration, 2),
            dubbing_script=results,
            ai_total_usage=usage_info
        )

        return final_result.model_dump()