# ai_services/narration/context_enhancer.py
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from ai_services.rag.schemas import Scene


class ContextEnhancer:
    """
    [Stage 2] 核心业务逻辑：清洗 RAG 碎片，结合本地蓝图重组上下文。
    """

    def __init__(self, blueprint_path: Path, logger: logging.Logger):
        self.logger = logger
        self.blueprint_data = self._load_blueprint(blueprint_path)
        self.scenes_map = self.blueprint_data.get("scenes", {})
        self.timeline_map = self._build_timeline_map()

    def _load_blueprint(self, path: Path) -> Dict:
        if not path.is_file():
            raise FileNotFoundError(f"Narrative blueprint not found at: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _build_timeline_map(self) -> Dict[int, int]:
        scene_id_to_rank = {}
        sequence = self.blueprint_data.get("narrative_timeline", {}).get("sequence", {})
        for key, val in sequence.items():
            try:
                sid = int(key)
                rank = val.get("narrative_index", 0)
                scene_id_to_rank[sid] = rank
            except (ValueError, TypeError):
                continue
        return scene_id_to_rank

    def extract_scene_id(self, source_uri: str) -> Optional[int]:
        if not source_uri: return None
        match = re.search(r"_scene_(\d+)_enhanced\.txt", source_uri)
        return int(match.group(1)) if match else None

    def enhance(self, retrieved_chunks: List[Any], config: Dict[str, Any]) -> str:
        self.logger.info(">>> ContextEnhancer: Starting enhancement...")

        # 1. Extract & Deduplicate
        hit_scene_ids = set()
        for chunk in retrieved_chunks:
            # 兼容不同 SDK 版本的属性名
            uri = getattr(chunk, 'source_uri', getattr(chunk, 'source_ref', ''))
            sid = self.extract_scene_id(uri)
            if sid: hit_scene_ids.add(sid)

        # 2. Scope Filtering
        scope = config.get("control_params", {}).get("scope", {})
        valid_scene_ids = []
        if scope.get("type") == "episode_range":
            start_ep, end_ep = scope.get("value", [1, 9999])
            for sid in hit_scene_ids:
                scene_data = self.scenes_map.get(str(sid))
                if scene_data and start_ep <= scene_data.get("chapter_id", 0) <= end_ep:
                    valid_scene_ids.append(sid)
        else:
            valid_scene_ids = list(hit_scene_ids)

        # 3. Timeline Sorting
        sorted_ids = sorted(valid_scene_ids, key=lambda x: self.timeline_map.get(x, 99999))
        self.logger.info(f"Selected Scenes: {sorted_ids}")

        # 4. Reconstruct
        final_context_parts = []
        series_name = self.blueprint_data.get("project_metadata", {}).get("project_name", "Unknown")
        lang = config.get("lang", "zh")

        for sid in sorted_ids:
            scene_data = self.scenes_map.get(str(sid))
            if not scene_data: continue
            try:
                scene_obj = Scene(**scene_data)
                rich_text = scene_obj.to_rag_text(series_id=series_name, lang=lang)
                final_context_parts.append(rich_text)
                final_context_parts.append("\n" + "=" * 30 + "\n")
            except Exception as e:
                self.logger.error(f"Failed to reconstruct Scene {sid}: {e}")

        return "\n".join(final_context_parts) if final_context_parts else "(No relevant scenes found.)"