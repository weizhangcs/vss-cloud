import json
import subprocess
import re
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import pysrt


class VSSVideoSlicer:
    """
    [VSS-Edge Core] 视频智能切片器
    职责：
    1. 利用 FFmpeg 高效提取场景变化、静音、时长信息。
    2. 结合字幕时间轴，生成基础切片。
    3. 执行“混合模态路由”：标记哪些切片需要文本分析，哪些需要视觉推理。
    """

    def __init__(self, video_path: str, subtitle_path: str):
        self.video_path = Path(video_path)
        self.subtitle_path = Path(subtitle_path)
        self.ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

        # 配置阈值
        self.MIN_VISUAL_DURATION = 2.0  # [策略] 只有大于2秒的空隙才值得做视觉推理
        self.SCENE_THRESHOLD = 0.3  # [FFmpeg] 场景变化阈值 (0-1)
        self.SILENCE_DB = -40  # [FFmpeg] 静音阈值
        self.SILENCE_DURATION = 0.5  # [FFmpeg] 持续多久算静音

    def _run_ffmpeg_filter(self, filter_chain: str) -> str:
        """运行 FFmpeg 滤镜并获取 stderr 输出"""
        cmd = [
            self.ffmpeg_bin,
            '-i', str(self.video_path),
            '-filter_complex', filter_chain,
            '-f', 'null',
            '-'
        ]
        # 增加 bufsize 避免管道阻塞，使用 text mode
        process = subprocess.Popen(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, encoding='utf-8', errors='ignore'
        )
        _, stderr = process.communicate()
        return stderr

    def detect_scene_changes(self) -> List[float]:
        """[高效] 使用 FFmpeg scdet 检测场景变化时间点"""
        print(f"Running Scene Detection (threshold={self.SCENE_THRESHOLD})...")
        # filter: select='gt(scene,0.3)',showinfo
        filter_cmd = f"select='gt(scene,{self.SCENE_THRESHOLD})',showinfo"
        log = self._run_ffmpeg_filter(f"[0:v]{filter_cmd}[outv]")

        # 解析日志: "pts_time:12.45"
        timestamps = []
        for line in log.splitlines():
            if "pts_time:" in line and "showinfo" in line:
                match = re.search(r'pts_time:([0-9.]+)', line)
                if match:
                    timestamps.append(float(match.group(1)))
        return sorted(list(set(timestamps)))

    def detect_audio_silence(self) -> List[Dict[str, float]]:
        """[高效] 使用 FFmpeg silencedetect 检测静音区间"""
        print(f"Running Silence Detection (db={self.SILENCE_DB})...")
        # filter: silencedetect=noise=-40dB:d=0.5
        filter_cmd = f"silencedetect=noise={self.SILENCE_DB}dB:d={self.SILENCE_DURATION}"
        log = self._run_ffmpeg_filter(f"[0:a]{filter_cmd}[outa]")

        silence_starts = []
        intervals = []

        # 解析日志
        # [silencedetect @ ...] silence_start: 45.23
        # [silencedetect @ ...] silence_end: 48.10 | silence_duration: 2.87
        for line in log.splitlines():
            if "silence_start" in line:
                match = re.search(r'silence_start: ([0-9.]+)', line)
                if match:
                    silence_starts.append(float(match.group(1)))
            elif "silence_end" in line and silence_starts:
                match = re.search(r'silence_end: ([0-9.]+)', line)
                if match:
                    end = float(match.group(1))
                    start = silence_starts.pop(0)  # 配对
                    intervals.append({"start": start, "end": end})

        return intervals

    def get_video_duration(self) -> float:
        """获取视频总时长"""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(self.video_path)
        ]
        try:
            output = subprocess.check_output(cmd).decode().strip()
            return float(output)
        except:
            return 0.0

    def process(self) -> Dict[str, Any]:
        """主处理逻辑：生成切片并路由"""

        # 1. 获取基础数据
        duration = self.get_video_duration()
        scene_changes = self.detect_scene_changes()
        # silence_intervals = self.detect_audio_silence() # 暂时备用，可视情况混入逻辑

        subs = pysrt.open(str(self.subtitle_path))

        slices = []
        last_time = 0.0

        print("Merging signals and generating slices...")

        # ---------------------------------------------------------
        # 核心算法：以字幕为主轴，填充空隙 (Gap Filling Strategy)
        # ---------------------------------------------------------

        for sub in subs:
            # Pysrt time to float seconds
            start_sec = sub.start.ordinal / 1000.0
            end_sec = sub.end.ordinal / 1000.0
            text_content = sub.text.replace('\n', ' ').strip()

            # A. 处理上一段字幕到这一段字幕之间的空隙 (Pre-Gap)
            if start_sec > last_time:
                gap_start = last_time
                gap_end = start_sec
                gap_duration = gap_end - gap_start

                # [路由逻辑]：检查空隙
                if gap_duration >= self.MIN_VISUAL_DURATION:
                    # 空隙足够长 -> 这是一个视觉切片 (Visual Segment)
                    # 检查是否有场景突变在空隙中间，如果有，拆分它
                    internal_cuts = [t for t in scene_changes if gap_start + 0.5 < t < gap_end - 0.5]

                    if internal_cuts:
                        # 有转场，拆分
                        current_gap_ptr = gap_start
                        for cut in internal_cuts:
                            slices.append({
                                "start_time": round(current_gap_ptr, 3),
                                "end_time": round(cut, 3),
                                "type": "visual_segment",
                                "processing_strategy": "visual_inference",  # <--- 路由给 Gemini VLM
                                "text_content": None,
                                "metadata": {"reason": "gap_with_scene_change"}
                            })
                            current_gap_ptr = cut
                        # 最后一小段
                        slices.append({
                            "start_time": round(current_gap_ptr, 3),
                            "end_time": round(gap_end, 3),
                            "type": "visual_segment",
                            "processing_strategy": "visual_inference",
                            "text_content": None,
                            "metadata": {"reason": "gap_remain"}
                        })
                    else:
                        # 无转场，是一个完整的长镜头/空镜
                        slices.append({
                            "start_time": round(gap_start, 3),
                            "end_time": round(gap_end, 3),
                            "type": "visual_segment",
                            "processing_strategy": "visual_inference",  # <--- 路由给 Gemini VLM
                            "text_content": None,
                            "metadata": {"reason": "long_gap"}
                        })
                else:
                    # 空隙太短 (e.g. 0.5s) -> 忽略或归并到下一句
                    # 这里简单的做忽略处理，或者标记为 gap
                    # 在实际剪辑中，这部分可能会被“吸附”到字幕切片中
                    pass

            # B. 处理字幕切片本身 (Dialogue)
            slices.append({
                "start_time": round(start_sec, 3),
                "end_time": round(end_sec, 3),
                "type": "dialogue",
                "processing_strategy": "text_analysis",  # <--- 路由给 LLM 语义分析
                "text_content": text_content,
                "metadata": {"source": "subtitle"}
            })

            last_time = end_sec

        # 处理尾部空隙
        if last_time < duration:
            gap_duration = duration - last_time
            if gap_duration >= self.MIN_VISUAL_DURATION:
                slices.append({
                    "start_time": round(last_time, 3),
                    "end_time": round(duration, 3),
                    "type": "visual_segment",
                    "processing_strategy": "visual_inference",
                    "text_content": None,
                    "metadata": {"reason": "tail_gap"}
                })

        # 最终封装
        result = {
            "video_path": str(self.video_path),
            "total_duration": duration,
            "slices": slices,
            "stats": {
                "total_slices": len(slices),
                "dialogue_count": len([s for s in slices if s['type'] == 'dialogue']),
                "visual_segment_count": len([s for s in slices if s['type'] == 'visual_segment'])
            }
        }
        return result


# --- 测试入口 ---
if __name__ == "__main__":
    import argparse

    # 简单的 mock 数据生成 (如果不想传参数)
    # 你可以手动修改这里的路径来跑测试

    parser = argparse.ArgumentParser(description="VSS Edge Core Slicer Test")
    parser.add_argument("--video", help="Path to video file", required=False)
    parser.add_argument("--sub", help="Path to subtitle file", required=False)
    parser.add_argument("--out", help="Output JSON path", default="vss_edge_slices.json")

    args = parser.parse_args()

    if args.video and args.sub:
        slicer = VSSVideoSlicer(args.video, args.sub)
        result = slicer.process()

        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"✅ Slicing complete! Output saved to {args.out}")
        print(f"Stats: {json.dumps(result['stats'], indent=2)}")

    else:
        print("No input provided. Using dry-run mode to check dependencies.")
        if shutil.which("ffmpeg"):
            print("ffmpeg detected: OK")
        else:
            print("ffmpeg NOT found. Please install ffmpeg.")
        try:
            import pysrt

            print("pysrt detected: OK")
        except ImportError:
            print("pysrt NOT found. Please pip install pysrt")