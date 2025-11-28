# ai_services/dubbing/dubbing_engine.py

import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from .strategies.base_strategy import TTSStrategy, ReplicationStrategy


class DubbingEngine:
    SERVICE_NAME = "dubbing_engine"

    def __init__(self,
                 logger: logging.Logger,
                 work_dir: Path,
                 strategies: Dict[str, TTSStrategy],
                 templates: Dict[str, Any],
                 metadata_dir: Path,
                 shared_root_path: Path):

        self.logger = logger
        self.work_dir = work_dir
        self.strategies = strategies
        self.templates = templates
        self.shared_root_path = shared_root_path

        # [移除] self.segmenter = ... (不再需要全局切分器)
        # 加载 instructs 保持不变
        self._load_instructs(metadata_dir / "tts_instructs.json")

        self.logger.info("DubbingEngine initialized (Strategy-First Mode).")

    def _load_instructs(self, path: Path):
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                self.instructs = json.load(f)
        else:
            self.instructs = {}

    def execute(self, narration_path: Path, template_name: str, **kwargs) -> Dict[str, Any]:
        # ... (前序加载 Template / Strategy / Replication Setup 逻辑保持不变) ...
        # 复制之前的 _handle_replication_setup 等逻辑

        template = self.templates.get(template_name)
        provider = template.get("provider")
        strategy = self.strategies.get(provider)
        if not strategy: raise ValueError(f"Strategy {provider} not found")

        base_params = template.get("params", {}).copy()
        base_params.update(kwargs)

        # 处理 Replication
        if template.get("method") == "replication":
            self._handle_replication_setup(strategy, template, base_params)

        # 处理 Style Instruct
        target_style = kwargs.get("style", "objective")
        lang = kwargs.get("lang", "zh")
        instruct_text = self.instructs.get(lang, {}).get(target_style, "")
        if instruct_text:
            base_params["instruct"] = instruct_text

        # --- 核心循环 (极简版) ---
        with narration_path.open('r', encoding='utf-8') as f:
            narration_data = json.load(f).get("narration_script", [])

        results = []
        total = len(narration_data)
        self.logger.info(f"Starting dubbing loop for {total} clips...")

        for idx, entry in enumerate(narration_data):
            self.logger.info(f"Processing clip {idx + 1}/{total}...")

            # [核心修改] 差异化数据源选择
            if provider == "google_tts":
                # Google 专用逻辑：
                # 1. 优先使用带 [tag] 的 narration_for_audio
                text = entry.get("narration_for_audio") or entry.get("narration", "")

                # 2. 注入动态指令 (通常是英文)
                current_params = base_params.copy()
                if entry.get("tts_instruct"):
                    current_params["instruct"] = entry.get("tts_instruct")

            else:
                # Aliyun/其他 专用逻辑：
                # 1. 强制使用纯净文本 (narration)，防止 [sigh] 等标记污染
                text = entry.get("narration", "")

                # 2. 忽略英文动态指令，保持使用全局样式 (base_params)
                # 因为 Aliyun 通常需要中文 Prompt，且不支持 Google 的复杂情感描述
                current_params = base_params.copy()

            if not text:
                self.logger.warning(f"Skipping clip {idx}: No text found.")
                continue

            # 文件路径 (扩展名由 Template 配置决定，Google配mp3，Aliyun配wav，无需代码干预)
            ext = template.get('audio_format', 'mp3')
            final_path = self.work_dir / f"narration_{idx:03d}.{ext}"

            try:
                # 调用策略
                duration = strategy.synthesize(text, final_path, current_params)

                rel_path = final_path.relative_to(self.shared_root_path)
                results.append({
                    **entry,
                    "audio_file_path": str(rel_path),
                    "duration_seconds": round(duration, 2)
                })
                self.logger.info(f"   ✅ Generated: {duration}s")

            except Exception as e:
                self.logger.error(f"   ❌ Failed: {e}")
                results.append({**entry, "error": str(e)})

        return {"dubbing_script": results}

    # ... (_handle_replication_setup 保持不变) ...
    def _handle_replication_setup(self, strategy, template, params):
        # (同之前代码)
        if not isinstance(strategy, ReplicationStrategy):
            raise TypeError("Strategy does not support replication.")
        source = template.get("replication_source")
        ref_path = self.shared_root_path / source['audio_path']
        ref_id = strategy.upload_reference_audio(ref_path, source['text'])
        params['reference_audio_id'] = ref_id