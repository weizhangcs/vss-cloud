import shutil
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Any


class MockSubItem:
    """模拟字幕条目对象"""

    def __init__(self, start_sec, end_sec, text):
        # 兼容旧逻辑的 ordinal (毫秒)
        self.start = type('obj', (object,), {'ordinal': int(start_sec * 1000)})
        self.end = type('obj', (object,), {'ordinal': int(end_sec * 1000)})
        self.text = text


class VSSASSSlicer:
    """
    [Core Algorithm] ASS 专用智能切片器 (Gap Filling Strategy)
    该类不依赖具体项目路径，未来可直接移植到 VSS Edge。
    """

    def __init__(self, video_path: str, ass_path: str):
        self.video_path = Path(video_path)
        self.subtitle_path = Path(ass_path)
        self.ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

        # 核心参数 (可配置)
        self.MIN_VISUAL_DURATION = 2.0  # 最小视觉片段时长
        self.SCENE_THRESHOLD = 0.3  # 场景检测阈值

    def _parse_ass_time(self, time_str):
        """解析 ASS 时间格式 H:MM:SS.cc -> 秒"""
        try:
            parts = time_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            s, cs = map(int, parts[2].split('.'))
            return h * 3600 + m * 60 + s + (cs / 100.0)
        except:
            return 0.0

    def load_subs_from_ass(self):
        """解析 ASS 字幕文件"""
        subs = []
        if not self.subtitle_path.exists():
            print(f"⚠️ ASS file not found: {self.subtitle_path}")
            return subs

        with open(self.subtitle_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 解析 Dialogue 行
        # Format: Dialogue: 0,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        for line in lines:
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    start_sec = self._parse_ass_time(parts[1].strip())
                    end_sec = self._parse_ass_time(parts[2].strip())
                    speaker = parts[4].strip()
                    content = parts[9].strip().replace("\\N", " ")

                    # 组合角色与文本
                    full_text = f"[{speaker}]: {content}"
                    subs.append(MockSubItem(start_sec, end_sec, full_text))

        # 按时间排序
        subs.sort(key=lambda x: x.start.ordinal)
        return subs

    def _run_ffmpeg_filter(self, filter_chain: str) -> str:
        cmd = [self.ffmpeg_bin, '-i', str(self.video_path), '-filter_complex', filter_chain, '-f', 'null', '-']
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, encoding='utf-8',
                                   errors='ignore')
        _, stderr = process.communicate()
        return stderr

    def detect_scene_changes(self) -> List[float]:
        print(f"Running Scene Detection (threshold={self.SCENE_THRESHOLD})...")
        filter_cmd = f"select='gt(scene,{self.SCENE_THRESHOLD})',showinfo"
        log = self._run_ffmpeg_filter(f"[0:v]{filter_cmd}[outv]")

        timestamps = []
        for line in log.splitlines():
            if "pts_time:" in line and "showinfo" in line:
                match = re.search(r'pts_time:([0-9.]+)', line)
                if match: timestamps.append(float(match.group(1)))
        return sorted(list(set(timestamps)))

    def get_video_duration(self) -> float:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
               "default=noprint_wrappers=1:nokey=1", str(self.video_path)]
        try:
            return float(subprocess.check_output(cmd).decode().strip())
        except:
            return 0.0

    def process(self) -> Dict[str, Any]:
        """主处理流程"""
        duration = self.get_video_duration()
        scene_changes = self.detect_scene_changes()
        subs = self.load_subs_from_ass()

        slices = []
        last_time = 0.0

        print(f"Merging signals ({len(subs)} lines) and generating slices...")

        for sub in subs:
            start_sec = sub.start.ordinal / 1000.0
            end_sec = sub.end.ordinal / 1000.0
            text_content = sub.text

            # --- Gap Filling Logic ---
            if start_sec > last_time:
                gap_start = last_time
                gap_end = start_sec
                gap_duration = gap_end - gap_start

                if gap_duration >= self.MIN_VISUAL_DURATION:
                    internal_cuts = [t for t in scene_changes if gap_start + 0.5 < t < gap_end - 0.5]
                    if internal_cuts:
                        current_gap_ptr = gap_start
                        for cut in internal_cuts:
                            slices.append({
                                "start_time": round(current_gap_ptr, 3),
                                "end_time": round(cut, 3),
                                "type": "visual_segment",
                                "text_content": None
                            })
                            current_gap_ptr = cut
                        slices.append({
                            "start_time": round(current_gap_ptr, 3),
                            "end_time": round(gap_end, 3),
                            "type": "visual_segment",
                            "text_content": None
                        })
                    else:
                        slices.append({
                            "start_time": round(gap_start, 3),
                            "end_time": round(gap_end, 3),
                            "type": "visual_segment",
                            "text_content": None
                        })

            # --- Dialogue Logic ---
            slices.append({
                "start_time": round(start_sec, 3),
                "end_time": round(end_sec, 3),
                "type": "dialogue",
                "text_content": text_content
            })
            last_time = end_sec

        # --- Tail Gap ---
        if last_time < duration:
            if duration - last_time >= self.MIN_VISUAL_DURATION:
                slices.append({
                    "start_time": round(last_time, 3),
                    "end_time": round(duration, 3),
                    "type": "visual_segment",
                    "text_content": None
                })

        return {
            "video_path": str(self.video_path),
            "total_duration": duration,
            "slices": slices,
            "stats": {
                "total_slices": len(slices)
            }
        }