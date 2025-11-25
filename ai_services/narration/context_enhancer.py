# ai_services/narration/context_enhancer.py

import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

# 引入 Schema 用于生成标准化的 RAG 文本格式 (Metadata Block)
from ai_services.rag.schemas import Scene


class ContextEnhancer:
    """
    [Stage 2] 上下文增强器。

    职责：
        解决 RAG 检索结果的“碎片化”和“乱序”问题。
        通过文件名溯源，回查本地完整蓝图，重组出有序、完整、无幻觉的剧情上下文。
    """

    def __init__(self, blueprint_path: Path, logger: logging.Logger):
        """
        Args:
            blueprint_path: 本地 narrative_blueprint.json 的绝对路径。
            logger: 日志记录器。
        """
        self.logger = logger
        self.blueprint_data = self._load_blueprint(blueprint_path)

        # 预加载数据映射以提升性能
        self.scenes_map = self.blueprint_data.get("scenes", {})
        self.timeline_map = self._build_timeline_map()

    def _load_blueprint(self, path: Path) -> Dict:
        if not path.is_file():
            raise FileNotFoundError(f"Narrative blueprint not found at: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _build_timeline_map(self) -> Dict[int, int]:
        """
        构建 {scene_id: sort_rank} 的映射表。

        依据：使用蓝图中的 narrative_timeline.sequence 字段，
        确保场景按照人工标注的正确叙事顺序排列，而非 RAG 的相似度顺序。
        """
        scene_id_to_rank = {}
        # sequence 结构示例: "1": {"narrative_index": 1}
        sequence = self.blueprint_data.get("narrative_timeline", {}).get("sequence", {})

        for key, val in sequence.items():
            try:
                sid = int(key)
                # 使用 narrative_index 作为排序权重
                rank = val.get("narrative_index", 0)
                scene_id_to_rank[sid] = rank
            except (ValueError, TypeError):
                continue
        return scene_id_to_rank

    def extract_scene_id(self, source_uri: str) -> Optional[int]:
        """
        核心算法：从 GCS URI 中提取 Scene ID。

        原理：部署时的文件名规范为 '{series_id}_scene_{id}_enhanced.txt'。
        该方法不依赖文件内容，仅依赖文件名，因此对 RAG 的截断分块免疫。
        """
        if not source_uri:
            return None
        match = re.search(r"_scene_(\d+)_enhanced\.txt", source_uri)
        return int(match.group(1)) if match else None

    def enhance(self, retrieved_chunks: List[Any], config: Dict[str, Any], asset_id: str) -> str:
        """
        执行上下文增强的标准流程。

        Pipeline:
            1. Extract: 从 chunk URI 提取 ID。
            2. Deduplicate: 去除重复命中的场景。
            3. Filter: 根据 scope (集数范围) 剔除无关场景。
            4. Sort: 根据 narrative_timeline 纠正顺序。
            5. Reconstruct: 使用本地蓝图的全量数据生成最终文本。
        """
        self.logger.info(">>> ContextEnhancer: Starting enhancement...")

        # --- 1. 提取 ID 并去重 (Extract & Deduplicate) ---
        hit_scene_ids = set()
        for chunk in retrieved_chunks:
            # 兼容 Vertex AI SDK 不同版本的属性名 (source_uri vs source_ref)
            uri = getattr(chunk, 'source_uri', getattr(chunk, 'source_ref', ''))
            sid = self.extract_scene_id(uri)
            if sid:
                hit_scene_ids.add(sid)
            else:
                self.logger.debug(f"Skipping chunk with unparsable URI: {uri}")

        # --- 2. 范围过滤 (Scope Filtering) ---
        scope = config.get("control_params", {}).get("scope", {})
        valid_scene_ids = []

        if scope.get("type") == "episode_range":
            # 默认范围为全集 [1, 9999]
            start_ep, end_ep = scope.get("value", [1, 9999])
            self.logger.info(f"Applying scope filter: Episode {start_ep}-{end_ep}")

            for sid in hit_scene_ids:
                scene_data = self.scenes_map.get(str(sid))
                if scene_data:
                    chapter_id = scene_data.get("chapter_id", 0)
                    if start_ep <= chapter_id <= end_ep:
                        valid_scene_ids.append(sid)
        else:
            # 如果没有范围限制，保留所有命中场景
            valid_scene_ids = list(hit_scene_ids)

        # --- 3. 时序排序 (Timeline Sorting) ---
        # 未在 timeline_map 中找到的场景将被排在最后 (权重 99999)
        sorted_ids = sorted(valid_scene_ids, key=lambda x: self.timeline_map.get(x, 99999))
        self.logger.info(f"Final scene sequence: {sorted_ids}")

        # --- 4. 内容重组 (Reconstruction) ---
        final_context_parts = []
        # [注意] 这里我们不再强依赖 blueprint 里的 project_name 来做 RAG 标识
        #series_name = self.blueprint_data.get("project_metadata", {}).get("project_name", "Unknown")
        lang = config.get("lang", "zh")

        for sid in sorted_ids:
            scene_data = self.scenes_map.get(str(sid))
            if not scene_data:
                continue

            try:
                # 利用 Pydantic Model 生成标准化文本
                scene_obj = Scene(**scene_data)
                # [核心修改] 传入 asset_id (UUID) 以生成一致的上下文
                rich_text = scene_obj.to_rag_text(asset_id=asset_id, lang=lang)
                final_context_parts.append(rich_text)
                # 添加分隔符，帮助 LLM 区分场景边界
                final_context_parts.append("\n" + "=" * 30 + "\n")
            except Exception as e:
                self.logger.error(f"Failed to reconstruct Scene {sid}: {e}")

        if not final_context_parts:
            return "(No relevant scenes found based on current criteria.)"

        return "\n".join(final_context_parts)