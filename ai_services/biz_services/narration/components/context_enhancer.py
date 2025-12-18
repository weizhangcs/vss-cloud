import re
import logging
from typing import List, Dict, Any, Optional
from collections import defaultdict

from ai_services.biz_services.narrative_dataset import NarrativeDataset, NarrativeFunction, StoryNode
from ai_services.biz_services.narration.schemas import NarrationServiceConfig


class ContextEnhancer:
    """
    [Stage 2] 上下文增强器 (Narrative Logic Injector).
    Updates:
    - [i18n Support]: 使用 prompt_definitions 生成多语言叙事描述。
    - [Surgical Injection]: 将叙事逻辑精准插入到 RAG 元数据块中。
    """

    def __init__(self,
                 dataset: NarrativeDataset,
                 prompt_definitions: Dict,  # [New] 接收定义
                 logger: logging.Logger):
        self.logger = logger
        self.dataset = dataset
        self.prompt_definitions = prompt_definitions
        self.logger.info(f"ContextEnhancer bound to asset: {dataset.project_metadata.asset_name}")

    def enhance(self, retrieved_chunks: List[Any], config: NarrationServiceConfig) -> str:
        if not retrieved_chunks:
            return "(No RAG chunks provided.)"

        # 1. 提取并分组
        scene_map = self._group_chunks_by_scene(retrieved_chunks)
        if not scene_map:
            # 兜底：无法提取ID时返回原始内容
            return "\n\n".join([self._get_chunk_text(c) for c in retrieved_chunks])

        hit_ids = list(scene_map.keys())

        # 2. 叙事排序
        if self.dataset.narrative_storyline and self.dataset.narrative_storyline.branches:
            target_branch_id = self.dataset.narrative_storyline.root_branch_id
            sorted_nodes = self._sort_by_storyline(hit_ids, target_branch_id)
        else:
            sorted_nodes = self._sort_by_physical_id(hit_ids)

        # 3. 组装上下文 (传入语言参数)
        return self._assemble_context(sorted_nodes, scene_map, lang=config.lang)

    def _assemble_context(self, sorted_nodes: List[StoryNode], scene_map: Dict[str, List[str]], lang: str) -> str:
        parts = []

        # 获取对应语言的定义，兜底 'zh'
        definitions = self.prompt_definitions.get(lang, self.prompt_definitions.get('zh', {}))
        narrative_defs = definitions.get("narrative_context", {})

        for node in sorted_nodes:
            sid_str = str(node.local_id)
            chunks = scene_map.get(sid_str, [])
            if not chunks: continue

            # [Step 1] 生成叙事脉络句子 (Narrative Line)
            narrative_line = self._build_narrative_line(node, narrative_defs)

            # [Step 2] 处理每个 Chunk，执行精准插入
            processed_chunks = []
            for chunk_text in chunks:
                injected_text = self._inject_narrative_line(chunk_text, narrative_line, lang)
                processed_chunks.append(injected_text)

            scene_content = "\n".join(processed_chunks)
            parts.append(scene_content)

        header = "=== RETRIEVED CONTEXT (Narrative Sorted) ===\n"
        return header + "\n" + "\n\n".join(parts) + "\n==================================="

    def _build_narrative_line(self, node: StoryNode, narrative_defs: Dict) -> str:
        """
        构建类似："本场景的叙事脉络是：Main 分支 第 5 幕，闪回片段，关联第 102 幕，这是一段过去的回忆。"
        """
        branch_name = "Main"  # 默认为 Main，如果有 Branch 对象可获取真实名称

        # 1. 获取 Function 描述
        func_key = node.narrative_function.value
        function_desc = narrative_defs.get("functions", {}).get(func_key, func_key)

        # 2. 获取 Cue 描述
        cue_desc = narrative_defs.get("cues", {}).get(func_key, "")

        # 3. 获取 Relation 描述
        if node.ref_scene_id:
            tpl = narrative_defs.get("relation_template", "Related to {ref_id}")
            relation_desc = tpl.format(ref_id=node.ref_scene_id)
        else:
            relation_desc = narrative_defs.get("no_relation", "")

        # 4. 填充主模版
        main_tpl = narrative_defs.get("template", "{branch_name} {seq} {function_desc}")

        try:
            line = main_tpl.format(
                branch_name=branch_name,
                seq=node.narrative_index,
                function_desc=function_desc,
                relation_desc=relation_desc,
                cue_desc=cue_desc
            )
            # 清理可能的连续逗号 (如果某些字段为空)
            line = re.sub(r'，\s*，', '，', line)
            line = re.sub(r',\s*,', ',', line)
            return line
        except Exception:
            return f"Narrative Info: Seq {node.narrative_index}, {func_key}"

    def _inject_narrative_line(self, chunk_text: str, narrative_line: str, lang: str) -> str:
        """
        [Surgical Injection]
        目标：插入到 "本场景的核心叙事是: ..." 之后，"---推理事实---" 之前。
        策略：寻找【推理事实块的头部】作为锚点，在它前面插入。
        """

        # 定义可能的锚点 (Anchor)，支持中英文
        # 这里的关键词要和 rag/schemas.json 里的 inference_header 对应
        # zh: "---推理事实---", en: "--- Inferred Facts ---"

        anchors = [
            r"---推理事实---",
            r"---\s*Inferred Facts\s*---",
            r"---\s*推理事实\s*---"
        ]

        # 尝试寻找锚点
        for anchor in anchors:
            match = re.search(anchor, chunk_text, re.IGNORECASE)
            if match:
                start_idx = match.start()
                # 在锚点之前插入
                # 格式： 原文... \n [插入行] \n ---推理事实---
                prefix = chunk_text[:start_idx].rstrip()
                suffix = chunk_text[start_idx:]
                return f"{prefix}\n{narrative_line}\n{suffix}"

        # [Fallback] 如果找不到锚点（比如该场景没有推理事实），则尝试追加到 Metadata 块末尾
        # 寻找第一个空行
        first_blank_line = chunk_text.find("\n\n")
        if first_blank_line != -1:
            prefix = chunk_text[:first_blank_line]
            suffix = chunk_text[first_blank_line:]
            return f"{prefix}\n{narrative_line}{suffix}"

        # [Ultimate Fallback] 放在最前面
        return f"{narrative_line}\n{chunk_text}"

    # --- 辅助方法 (保持不变) ---
    def _get_chunk_text(self, chunk: Any) -> str:
        if hasattr(chunk, 'text'): return chunk.text
        if hasattr(chunk, 'page_content'): return chunk.page_content
        if isinstance(chunk, dict): return chunk.get('text', chunk.get('content', ''))
        return str(chunk)

    def _group_chunks_by_scene(self, chunks: List[Any]) -> Dict[str, List[str]]:
        scene_map = defaultdict(list)
        for chunk in chunks:
            text = self._get_chunk_text(chunk)
            sid = self._extract_id_from_text(text)
            if sid:
                scene_map[str(sid)].append(text)
        return scene_map

    def _extract_id_from_text(self, text: str) -> Optional[int]:
        pattern = r"(?:场景ID|Scene\s*ID)\s*[:：]\s*(\d+)"
        match = re.search(pattern, text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _sort_by_storyline(self, scene_ids: List[str], branch_id: str) -> List[StoryNode]:
        branch = self.dataset.narrative_storyline.branches.get(branch_id)
        if not branch: return self._sort_by_physical_id(scene_ids)
        node_map = {str(node.local_id): node for node in branch.nodes}
        valid_nodes = []
        for sid in scene_ids:
            if sid in node_map:
                valid_nodes.append(node_map[sid])
            else:
                valid_nodes.append(StoryNode(local_id=int(sid), narrative_index=9999))
        return sorted(valid_nodes, key=lambda n: n.narrative_index)

    def _sort_by_physical_id(self, scene_ids: List[str]) -> List[StoryNode]:
        sorted_ids = sorted(scene_ids, key=lambda x: int(x))
        return [StoryNode(local_id=int(sid), narrative_index=i + 1) for i, sid in enumerate(sorted_ids)]