import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Dict, Any, List
from pydantic import BaseModel

from django.conf import settings
from ai_services.ai_platform.llm.mixins import AIServiceMixin
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from core.exceptions import BizException
from core.error_codes import ErrorCode
from .schemas import SubtitleContextPayload, SubtitleContextResult, OptimizedSubtitleItem


class SubtitleLine(BaseModel):
    index: int
    start_time: str
    end_time: str
    content: str


class SubtitleContextService(AIServiceMixin):
    """
    [Service] 字幕上下文服务 (v2 - 分批角色推理版)
    策略：
    1. 解析 SRT 为结构化列表。
    2. 压缩内容 (去时间戳)。
    3. 分批 (Batching) 喂给 AI，规避 Output Token 限制。
    4. 聚合结果。
    """

    # 批次大小：建议 100-200。
    # 太小成本高（重复 Input context），太大容易由 Output Limit 导致截断。
    BATCH_SIZE = 150

    def __init__(self, logger, gemini_processor: GeminiProcessor, cost_calculator: CostCalculator):
        self.logger = logger
        self.gemini_processor = gemini_processor
        self.cost_calculator = cost_calculator
        self.prompts_dir = Path(__file__).parent / "prompts"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.info("🚀 Starting Subtitle Role Inference (Batch Mode)...")

        task_input = SubtitleContextPayload(**payload)
        subtitle_full_path = self._resolve_path(task_input.subtitle_path)

        # 1. 解析 SRT
        if not subtitle_full_path.exists():
            raise BizException(ErrorCode.FILE_IO_ERROR, f"File not found: {subtitle_full_path}")

        raw_srt_content = subtitle_full_path.read_text(encoding='utf-8-sig')  # Handle BOM
        all_lines = self._parse_srt(raw_srt_content)
        total_lines = len(all_lines)

        self.logger.info(f"Parsed {total_lines} lines. Strategy: Batch Processing (Size={self.BATCH_SIZE})")

        # 2. 准备全局上下文 (Known Characters)
        chars_str = ", ".join(task_input.known_characters) if task_input.known_characters else "None (Infer from text)"

        final_results = []
        total_usage = {}

        # 3. 分批循环
        # 计算总批数
        num_batches = math.ceil(total_lines / self.BATCH_SIZE)

        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.BATCH_SIZE
            end_idx = min((batch_idx + 1) * self.BATCH_SIZE, total_lines)

            batch_lines = all_lines[start_idx:end_idx]

            self.logger.info(f"Processing Batch {batch_idx + 1}/{num_batches} (Lines {start_idx + 1}-{end_idx})...")

            # 3.1 压缩内容：只生成 "Index Content"
            compressed_text = "\n".join([f"{line.index} {line.content}" for line in batch_lines])

            # 3.2 构建 Prompt
            prompt = self._build_prompt(
                "role_inference_batch",  # 对应上面的新 Prompt 文件名
                lang=task_input.lang,
                character_list=chars_str,
                video_title=task_input.video_title or "Unknown",
                compressed_subtitles=compressed_text
            )

            # 3.3 调用 AI
            try:
                response_data, usage = self.gemini_processor.generate_content(
                    model_name=task_input.model_name,
                    prompt=prompt,
                    temperature=0.1,  # 极低温度，确保格式稳定
                    tools = None,  # <--- ⛔ 必须显式禁用工具
                    tool_config = None  # <--- ⛔ 必须显式禁用工具配置
                )

                # 累加 Cost
                self._calculate_and_merge_cost(task_input.model_name, usage, total_usage)

                # 3.4 解析映射
                mappings = response_data.get("mappings", [])

                # 转为 Dict 方便查找: {index: speaker}
                speaker_map = {m.get("i"): m.get("s", "Unknown") for m in mappings}

                # 3.5 回填到结果
                for line in batch_lines:
                    speaker = speaker_map.get(line.index, "Unknown")

                    # 构造最终 Output Item
                    # 注意：这里我们不做句式合并，只做角色识别，所以 content 是原始的
                    final_results.append(OptimizedSubtitleItem(
                        index=line.index,
                        start_time=self._srt_time_to_seconds(line.start_time),
                        end_time=self._srt_time_to_seconds(line.end_time),
                        content=line.content,
                        speaker=speaker,
                        reasoning="Batch Inferred"
                    ))

            except Exception as e:
                self.logger.error(f"Batch {batch_idx + 1} failed: {e}")

                # [新增] 黑匣子：打印出导致失败的原始内容，方便排查
                # 只打印前500个字符，避免日志爆炸
                self.logger.error(f"💀 FAILED BATCH CONTENT (First 1000 chars):\n{compressed_text[:1000]}")

                # 兜底：如果这一批失败了，填 Unknown，不要让整个任务挂掉
                for line in batch_lines:
                    final_results.append(OptimizedSubtitleItem(
                        index=line.index,
                        start_time=self._srt_time_to_seconds(line.start_time),
                        end_time=self._srt_time_to_seconds(line.end_time),
                        content=line.content,
                        speaker="Unknown (Error)",
                        reasoning="Inference Failed"
                    ))

        # =========================================================
        # Stage 3.5: Speaker Normalization (新增)
        # =========================================================
        self.logger.info("Stage 3.5: Normalizing Speaker Names...")

        # 1. 提取所有出现的原始名字
        raw_speakers = list(set([item.speaker for item in final_results if item.speaker != "Unknown"]))

        if len(raw_speakers) > 0:
            # 2. 调用 AI 生成映射表
            normalization_map = self._normalize_speakers_via_ai(
                raw_speakers,
                task_input.model_name,
                task_input.lang,
                total_usage
            )

            # 3. 应用映射 (In-place Update)
            update_count = 0
            for item in final_results:
                if item.speaker in normalization_map:
                    original = item.speaker
                    new_name = normalization_map[original]
                    if original != new_name:
                        item.speaker = new_name
                        update_count += 1

            self.logger.info(f"Normalized {update_count} lines based on {len(normalization_map)} mappings.")

        # =========================================================
        # Stage 4: Post Processing (SRT 还原 & 角色分析)
        # =========================================================
        self.logger.info("Stage 4: Post-Processing (SRT Generation & Metrics)...")

        # 4.1 生成 ASS 文件 (带 Speaker)
        output_ass_path = self._generate_ass_file(task_input.subtitle_path, final_results)

        # 4.2 计算角色指标 (适配版)
        metrics_report = self._calculate_metrics(final_results)

        # 5. 构造最终返回
        result = SubtitleContextResult(
            input_file=str(task_input.subtitle_path),
            optimized_subtitles=final_results,
            output_ass_path=str(output_ass_path),  # 返回路径
            character_roster=metrics_report.get("character_roster", []),  # 返回角色云
            stats={
                "total_lines": total_lines,
                "processed_lines": len(final_results),
                "batches": num_batches,
                "unique_characters": len(metrics_report.get("character_roster", []))
            },
            usage_report=total_usage
        )

        self.logger.info(f"✅ Batch Processing Complete. Cost: ${total_usage.get('total_cost_usd', 0):.4f}")
        return result.model_dump()

    def _parse_srt(self, content: str) -> List[SubtitleLine]:
        """简易 SRT 解析器"""
        # 统一换行符
        content = content.replace('\r\n', '\n').replace('\r', '\n')

        # SRT 块由空行分隔
        blocks = content.strip().split('\n\n')

        parsed_lines = []
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                try:
                    # Line 1: Index
                    idx = int(lines[0].strip())
                    # Line 2: Timecode
                    time_parts = lines[1].split(' --> ')
                    start, end = time_parts[0].strip(), time_parts[1].strip()
                    # Line 3+: Content
                    text = " ".join(lines[2:]).strip()  # 合并多行字幕文本

                    parsed_lines.append(SubtitleLine(
                        index=idx,
                        start_time=start,
                        end_time=end,
                        content=text
                    ))
                except Exception:
                    continue  # 跳过损坏的块

        return parsed_lines

    def _calculate_and_merge_cost(self, model_name: str, usage: Dict, total_usage: Dict):
        """累加成本 (修复版：增加类型安全检查)"""
        costs = self.cost_calculator.calculate(model_name, usage)

        # 1. 累加 Usage (Token数)
        for k, v in usage.items():
            # 排除 bool (True/False) 和 str (如 timestamp)，只累加 int/float
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total_usage[k] = total_usage.get(k, 0) + v

        # 2. 累加 Costs (金额)
        for k, v in costs.items():
            # [关键修复] 必须检查类型，过滤掉 'warning' 等字符串字段
            if isinstance(v, (int, float)):
                total_usage[k] = total_usage.get(k, 0) + v

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if p.is_absolute(): return p
        return settings.SHARED_ROOT / p

    def _srt_time_to_seconds(self, time_str: str) -> float:
        if not time_str: return 0.0
        try:
            time_str = time_str.replace(',', '.')
            hours, minutes, seconds = time_str.split(':')
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        except Exception:
            return 0.0

    # --- 新增辅助方法 ---

    def _generate_ass_file(self, original_path_str: str, items: List[OptimizedSubtitleItem]) -> Path:
        """生成 ASS 字幕文件 (支持 Speaker 字段)"""
        original_path = self._resolve_path(original_path_str)
        output_filename = f"{original_path.stem}_ai_labeled.ass"
        output_path = original_path.parent / output_filename

        def sec_to_ass_time(seconds: float) -> str:
            """12.345 -> 0:00:12.34 (H:MM:SS.cc)"""
            total_sec = int(seconds)
            cs = int((seconds - total_sec) * 100)  # Centiseconds
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        # ASS Header Template (Standard 1080p)
        header = """[Script Info]
Title: VSS AI Generated Subtitle
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        events = []
        for item in items:
            start_str = sec_to_ass_time(item.start_time)
            end_str = sec_to_ass_time(item.end_time)

            # 清洗角色名中的特殊字符，防止破坏 ASS 格式
            safe_speaker = item.speaker.replace(",", " ").strip() if item.speaker else "Unknown"
            # 清洗文本中的换行符
            safe_content = item.content.replace("\n", "\\N")

            # 构造 Event 行
            # Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            line = f"Dialogue: 0,{start_str},{end_str},Default,{safe_speaker},0,0,0,,{safe_content}"
            events.append(line)

        full_content = header + "\n".join(events)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        try:
            return output_path.relative_to(settings.SHARED_ROOT)
        except ValueError:
            return str(output_path)

    def _calculate_metrics(self, items: List[OptimizedSubtitleItem]) -> Dict:
        """
        [算法适配] 基于纯字幕流的角色重要度计算。
        由于没有 Scene，我们将 'Presence' 的定义退化为 '出现在多少句对话中'。
        """
        metrics = defaultdict(
            lambda: {
                "dialogue_count": 0,
                "dialogue_total_length": 0,
                "dialogue_total_duration": 0.0,
                "raw_names": set(),
            }
        )

        exclude_patterns = ["Unknown", "Narrator", "News", "Radio"]

        # 1. 统计基础数据
        for item in items:
            raw_speaker = item.speaker.strip()
            if not raw_speaker: continue
            if any(raw_speaker.startswith(p) for p in exclude_patterns): continue

            # 归一化 Key
            speaker_key = " ".join(raw_speaker.lower().split())

            metrics[speaker_key]["raw_names"].add(raw_speaker)
            metrics[speaker_key]["dialogue_count"] += 1
            metrics[speaker_key]["dialogue_total_length"] += len(item.content)

            dur = item.end_time - item.start_time
            metrics[speaker_key]["dialogue_total_duration"] += dur

        # 2. 计算得分
        roster = []
        if not metrics:
            return {"character_roster": []}

        target_chars = metrics.keys()

        def safe_max(iterable):
            val = max(iterable, default=0)
            return val if val > 0 else 1

        # 这里的 Max 基准只有三个维度 (去掉了 Scene 和 Interaction)
        max_vals = {
            "dialogue": safe_max(metrics[c]["dialogue_count"] for c in target_chars),
            "length": safe_max(metrics[c]["dialogue_total_length"] for c in target_chars),
            "duration": safe_max(metrics[c]["dialogue_total_duration"] for c in target_chars),
        }

        for key, data in metrics.items():
            # 简化版公式：只看话量和时长
            # presence_score 实际上就是活跃度
            score = (
                    (data["dialogue_count"] / max_vals["dialogue"]) * 0.4 +
                    (data["dialogue_total_length"] / max_vals["length"]) * 0.3 +
                    (data["dialogue_total_duration"] / max_vals["duration"]) * 0.3
            )

            display_name = list(data["raw_names"])[0] if data["raw_names"] else key

            roster.append({
                "name": display_name,
                "key": key,
                "weight_score": round(score, 4),  # 绝对分值 (0-1)
                "_raw_score": score,
                "stats": {
                    "lines": data["dialogue_count"],
                    "duration_sec": round(data["dialogue_total_duration"], 2)
                },
                "variations": list(data["raw_names"])
            })

        # 3. 排序与百分比
        roster.sort(key=lambda x: x["weight_score"], reverse=True)

        top_score = roster[0]["_raw_score"] if roster else 1
        for r in roster:
            pct = (r["_raw_score"] / top_score) * 100 if top_score > 0 else 0
            r["weight_percent"] = f"{round(pct, 1)}%"
            del r["_raw_score"]

        return {"character_roster": roster}

    def _normalize_speakers_via_ai(self, raw_names: List[str], model_name: str, lang: str, total_usage: Dict) -> Dict[
        str, str]:
        """调用 AI 进行名字归一化"""
        # 如果名字太少，不用 AI
        if len(raw_names) < 3:
            return {n: n for n in raw_names}

        # 构造 Prompt
        names_str = json.dumps(raw_names, indent=2)
        prompt = self._build_prompt(
            "speaker_normalization",  # 对应新建的 txt
            lang=lang,
            name_list=names_str
        )

        try:
            # 这是一个简单的任务，Flash 模型足够了
            response_data, usage = self.gemini_processor.generate_content(
                model_name=model_name,
                prompt=prompt,
                temperature=0.1,
                tools = None,  # <--- ⛔ 必须显式禁用工具
                tool_config = None  # <--- ⛔ 必须显式禁用工具配置
            )
            self._calculate_and_merge_cost(model_name, usage, total_usage)

            return response_data.get("normalization_map", {})

        except Exception as e:
            self.logger.error(f"Speaker Normalization failed: {e}")
            # 兜底：不改动
            return {}