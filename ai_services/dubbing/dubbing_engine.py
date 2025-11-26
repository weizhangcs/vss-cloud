# ai_services/dubbing/dubbing_engine.py

import json
import logging
import subprocess
import os
from pathlib import Path
from typing import Dict, Any, List

# 导入 Step 1 的切分器
from .text_segmenter import MultilingualTextSegmenter
# 导入 Step 2 的策略接口
from .strategies.base_strategy import TTSStrategy, ReplicationStrategy


class DubbingEngine:
    """
    智能配音引擎 (FFmpeg Native 版)。
    """
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

        # 初始化组件
        self.segmenter = MultilingualTextSegmenter(logger)
        self.instructs = self._load_json_config(metadata_dir / "tts_instructs.json")

        self.logger.info("DubbingEngine initialized (FFmpeg Native Mode).")

    def _load_json_config(self, path: Path) -> Dict:
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def execute(self, narration_path: Path, template_name: str, **kwargs) -> Dict[str, Any]:
        """
        执行配音生成主流程。
        """
        # 1. 加载输入数据
        with narration_path.open('r', encoding='utf-8') as f:
            input_data = json.load(f)
            narration_script = input_data.get("narration_script", [])
            config_snapshot = input_data.get("config_snapshot", {})
            default_style = config_snapshot.get("control_params", {}).get("style", "objective")

        # 2. 准备策略和参数
        template = self.templates.get(template_name)
        if not template:
            raise ValueError(f"Template '{template_name}' not found.")

        provider = template.get("provider")
        strategy = self.strategies.get(provider)
        if not strategy:
            raise ValueError(f"Strategy for provider '{provider}' not found.")

        # 基础参数
        base_params = template.get("params", {}).copy()
        base_params.update(kwargs)

        # 3. 处理语音复刻
        if template.get("method") == "replication":
            self._handle_replication_setup(strategy, template, base_params)

        # 4. 确定风格指令
        target_style = kwargs.get("style", default_style)
        lang = kwargs.get("lang", "zh")

        instruct_text = self.instructs.get(lang, {}).get(target_style, "")
        if instruct_text:
            base_params["instruct"] = instruct_text
            self.logger.info(f"Injecting TTS Instruct for style '{target_style}': {instruct_text[:20]}...")

        # 5. 执行生成循环
        results = []
        total = len(narration_script)

        for idx, item in enumerate(narration_script):
            text = item.get("narration", "")
            if not text: continue

            self.logger.info(f"--- Processing clip {idx + 1}/{total} ---")
            self.logger.info(f"Origin Text Length: {len(text)} chars")

            # 5.1 文本切分 (Segmentation)
            # [核心修改] 显式调整切分阈值，从 300 改为 150，增加切分粒度以方便调试
            # 并在日志中打印切分结果
            segments = self.segmenter.segment(text, lang=lang, max_len=90)

            self.logger.info(f"✂️  Segmentation Result: {len(segments)} parts")
            for i, s in enumerate(segments):
                self.logger.info(f"    [Part {i + 1}] ({len(s)} chars): {s[:100]}...")

            audio_segments_paths = []

            # 5.2 分段合成 (Synthesize Sub-clips)
            for seg_idx, seg_text in enumerate(segments):
                temp_filename = f"temp_{idx}_{seg_idx}.wav"
                temp_path = self.work_dir / temp_filename

                self.logger.info(f"    >> Synthesizing Part {seg_idx + 1}/{len(segments)} -> {temp_filename}")

                try:
                    # 调用策略生成音频
                    strategy.synthesize(seg_text, temp_path, base_params)

                    if temp_path.exists() and temp_path.stat().st_size > 0:
                        file_size_kb = temp_path.stat().st_size / 1024
                        self.logger.info(f"       ✅ Success. Size: {file_size_kb:.2f} KB")
                        audio_segments_paths.append(temp_path)
                    else:
                        self.logger.error(f"       ❌ Failed. File not created or empty.")
                except Exception as e:
                    self.logger.error(f"       ❌ Exception: {e}")

            # 5.3 音频拼接 (FFmpeg Merge)
            if not audio_segments_paths:
                self.logger.warning(f"No audio generated for clip {idx}")
                results.append({**item, "error": "Generation failed"})
                continue

            final_ext = template.get('audio_format', 'mp3')
            final_filename = f"narration_{idx:03d}.{final_ext}"
            final_path = self.work_dir / final_filename

            self.logger.info(f"    >> Merging {len(audio_segments_paths)} files -> {final_filename}")

            try:
                # 调用 FFmpeg 进行拼接
                duration = self._merge_audio_files_ffmpeg(audio_segments_paths, final_path)
                self.logger.info(f"       ✅ Merge Complete. Duration: {duration}s")
            except Exception as e:
                self.logger.error(f"       ❌ Merge Failed: {e}")
                results.append({**item, "error": f"Merge failed: {e}"})
                self._cleanup_temp_files(audio_segments_paths)
                continue

            # 清理临时分段文件
            self._cleanup_temp_files(audio_segments_paths)

            # 5.4 记录结果
            rel_path = final_path.relative_to(self.shared_root_path)
            results.append({
                **item,
                "audio_file_path": str(rel_path),
                "duration_seconds": round(duration, 2)
            })

        return {"dubbing_script": results}

    def _handle_replication_setup(self, strategy, template, params):
        """处理语音复刻的参考音频上传"""
        if not isinstance(strategy, ReplicationStrategy):
            raise TypeError("Strategy does not support replication.")

        source = template.get("replication_source")
        if not source:
            raise ValueError("Missing 'replication_source' in template.")

        ref_path = self.shared_root_path / source['audio_path']
        ref_text = source['text']

        self.logger.info(f"Uploading reference audio: {ref_path}")
        ref_id = strategy.upload_reference_audio(ref_path, ref_text)
        params['reference_audio_id'] = ref_id

    def _merge_audio_files_ffmpeg(self, paths: List[Path], output_path: Path) -> float:
        """
        使用 FFmpeg Concat Demuxer 拼接音频文件。
        """
        if not paths: return 0.0

        # 1. 创建 filelist.txt
        list_file_path = output_path.parent / f"{output_path.stem}_list.txt"

        try:
            with list_file_path.open("w", encoding="utf-8") as f:
                for p in paths:
                    # 必须使用 forward slash，即使在 Windows 上
                    f.write(f"file '{p.as_posix()}'\n")

            # 2. 构建 FFmpeg 命令
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file_path),
                "-y",
                str(output_path)
            ]

            # 3. 执行命令
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # 4. 获取时长
            duration = self._get_duration_ffprobe(output_path)
            return duration

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            raise RuntimeError(f"FFmpeg command failed: {error_msg}")
        finally:
            # 清理列表文件
            if list_file_path.exists():
                list_file_path.unlink()

    def _get_duration_ffprobe(self, file_path: Path) -> float:
        """使用 ffprobe 获取媒体文件时长"""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        try:
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return float(result.stdout.decode().strip())
        except Exception as e:
            self.logger.warning(f"Failed to get duration for {file_path}: {e}")
            return 0.0

    def _cleanup_temp_files(self, paths: List[Path]):
        for p in paths:
            try:
                if p.exists(): p.unlink()
            except Exception as e:
                self.logger.warning(f"Failed to delete temp file {p}: {e}")