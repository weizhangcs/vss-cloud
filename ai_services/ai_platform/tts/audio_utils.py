# ai_services/dubbing/audio_utils.py

import subprocess
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

def merge_audio_files_ffmpeg(paths: List[Path], output_path: Path) -> float:
    """
    使用 FFmpeg Concat Demuxer 拼接音频文件。
    """
    if not paths:
        return 0.0

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
            "-c", "copy", # [优化] 使用流拷贝，速度极快且不损耗音质
            str(output_path)
        ]

        # 3. 执行命令
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # 4. 获取时长
        return get_audio_duration(output_path)

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg command failed: {error_msg}")
    finally:
        # 清理列表文件
        if list_file_path.exists():
            list_file_path.unlink()

def get_audio_duration(file_path: Path) -> float:
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
        logger.warning(f"Failed to get duration for {file_path}: {e}")
        return 0.0