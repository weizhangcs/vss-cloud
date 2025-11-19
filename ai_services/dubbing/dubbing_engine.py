# ai_services/dubbing/dubbing_engine.py
import yaml
from pathlib import Path
from typing import Dict, Any, List
import json
import logging
# [修改] 移除 from tqdm import tqdm

# [修改] 导入新的依赖
from .strategies.base_strategy import TTSStrategy, ReplicationStrategy


# [修改] DubbingEngine 不再继承任何基类
class DubbingEngine:
    SERVICE_NAME = "dubbing_engine"

    # HAS_OWN_DATADIR 逻辑将由 Celery 任务处理

    def __init__(self,
                 logger: logging.Logger,
                 work_dir: Path,
                 strategies: Dict[str, TTSStrategy],
                 templates: Dict[str, Any],
                 shared_root_path: Path):
        """
        [重构] 初始化方法，接收所有外部依赖。

        Args:
            logger: Celery 任务传入的日志记录器。
            work_dir: 此任务专用的音频输出目录 (绝对路径)。
            strategies: 一个包含已实例化策略的字典。
            templates: 从 YAML 加载的模板配置字典。
            shared_root_path: 项目的共享根目录 (e.g., /app/shared_media)。
        """
        self.logger = logger
        self.work_dir = work_dir
        self.strategies = strategies
        self.templates = templates
        self.shared_root_path = shared_root_path  # 用于解析参考音频
        self.logger.info(
            f"DubbingEngine initialized for work_dir: {self.work_dir} with strategies: {list(self.strategies.keys())}")

    def execute(self, narration_path: Path, template_name: str, **kwargs) -> Dict[str, Any]:
        """
        [重构] 执行配音生成的核心逻辑。
        """
        self.logger.info(f"开始使用模板 '{template_name}' 生成配音...")

        template = self.templates.get(template_name)
        if not template:
            raise ValueError(f"模板 '{template_name}' 未定义。")

        provider_name = template.get("provider")
        strategy = self.strategies.get(provider_name)
        if not strategy:
            raise ValueError(f"提供商 '{provider_name}' 没有对应的实现策略。")

        params = template.get("params", {})
        method = template.get("method", "text")

        # [修改] 语音复刻的预处理逻辑
        if method == "replication":
            self.logger.info("Replication method detected. Starting pre-processing step...")
            if not isinstance(strategy, ReplicationStrategy):
                raise TypeError(
                    f"提供商 '{provider_name}' 的策略不支持 'replication' (非 ReplicationStrategy 实例)。")

            source_info = template.get("replication_source")
            if not source_info:
                raise ValueError("Replication模板必须包含 'replication_source' 配置。")

            # [核心路径修正] 从 shared_root_path 解析参考音频的绝对路径
            ref_audio_rel_path = source_info['audio_path']
            ref_audio_abs_path = self.shared_root_path / ref_audio_rel_path

            if not ref_audio_abs_path.is_file():
                raise FileNotFoundError(f"Replication source audio not found at: {ref_audio_abs_path}")

            text = source_info['text']
            self.logger.info(f"Uploading reference audio from: {ref_audio_abs_path}")

            # 调用策略上传
            reference_audio_id = strategy.upload_reference_audio(ref_audio_abs_path, text)
            self.logger.info(f"Successfully uploaded reference audio. Received ID: {reference_audio_id}")
            params['reference_audio_id'] = reference_audio_id

        # --- 配音循环 ---
        with narration_path.open('r', encoding='utf-8') as f:
            narration_data = json.load(f).get("narration_script", [])

        dubbing_results = []

        # [核心修改] 移除 tqdm，替换为 logger
        total_clips = len(narration_data)
        self.logger.info(f"Starting dubbing loop for {total_clips} clips...")

        for index, entry in enumerate(narration_data):
            # [核心修改] 使用 logger 报告进度
            self.logger.info(f"Processing clip {index + 1}/{total_clips}...")

            narration_text = entry.get("narration")
            if not narration_text:
                self.logger.warning(f"Skipping clip {index + 1} (no narration text found).")
                continue

            audio_format = template.get("audio_format", "mp3")
            audio_file_name = f"narration_{index:03d}.{audio_format}"

            # [核心路径修正] 音频文件保存到任务专属的 work_dir
            audio_file_path_abs = self.work_dir / audio_file_name

            try:
                duration_seconds = strategy.synthesize(
                    text=narration_text,
                    output_path=audio_file_path_abs,  # 策略使用绝对路径保存
                    params=params
                )

                # [核心路径修正] 计算相对于 shared_root 的路径，用于存入 JSON
                audio_file_path_rel = audio_file_path_abs.relative_to(self.shared_root_path)

                dubbing_results.append({
                    **entry,
                    "duration_seconds": round(duration_seconds, 3),
                    "audio_file_path": str(audio_file_path_rel)  # 存储相对路径
                })
            except Exception as e:
                self.logger.error(f"Failed to synthesize text: '{narration_text[:20]}...'", exc_info=True)
                dubbing_results.append({**entry, "duration_seconds": 0.0, "error": str(e)})

        # [修改] 不再保存文件，只返回最终的字典结构
        final_output = {"dubbing_script": dubbing_results}
        self.logger.info("Dubbing generation complete. Returning data structure.")
        return final_output