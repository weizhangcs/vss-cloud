# ai_services/narration/context_enhancer.py

import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from pydantic import ValidationError

# [核心升级] 引入 NarrativeBlueprint 进行强校验
from ai_services.ai_platform.rag.schemas import Scene, NarrativeBlueprint


class ContextEnhancer:
    """
    [Stage 2] 上下文增强器 (Hardened Version)。

    职责：清洗 RAG 碎片，回查本地蓝图，重组有序上下文。
    防御：
    1. 蓝图加载时执行 Pydantic 强校验。
    2. RAG Chunk 解析增加容错。
    """

    def __init__(self, blueprint_path: Path, logger: logging.Logger):
        self.logger = logger
        # [升级] 加载并校验蓝图，获取强类型的 blueprint 对象
        self.blueprint_model = self._load_and_validate_blueprint(blueprint_path)

        # 为了兼容旧逻辑，我们保留 scenes_map (dict)，但数据源来自校验后的 model
        # Pydantic model 转 dict, by_alias=True 确保字段名一致
        self.blueprint_data = self.blueprint_model.dict()
        self.scenes_map = self.blueprint_data.get("scenes", {})

        # 构建时间线映射
        self.timeline_map = self._build_timeline_map()

    def _load_and_validate_blueprint(self, path: Path) -> NarrativeBlueprint:
        """
        加载并强制校验蓝图文件结构。
        """
        if not path.is_file():
            raise FileNotFoundError(f"Blueprint file not found at: {path}")

        try:
            # 1. 尝试解析 JSON
            with path.open("r", encoding="utf-8") as f:
                raw_data = json.load(f)

            # 2. [核心防御] 使用 Pydantic 进行结构校验
            # 如果缺少 scenes, project_metadata 等关键字段，这里会直接爆出清晰的错误
            return NarrativeBlueprint(**raw_data)

        except json.JSONDecodeError as e:
            raise ValueError(f"Blueprint file is corrupted (Invalid JSON): {e}")
        except ValidationError as e:
            raise ValueError(f"Blueprint schema validation failed: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error loading blueprint: {e}")

    def _build_timeline_map(self) -> Dict[int, int]:
        """构建 {scene_id: sort_rank} 映射。"""
        scene_id_to_rank = {}
        # 使用安全的 .get 链式调用，防止 NoneType 错误
        timeline = self.blueprint_data.get("narrative_timeline") or {}
        sequence = timeline.get("sequence") or {}

        for key, val in sequence.items():
            try:
                sid = int(key)
                rank = val.get("narrative_index", 0)
                scene_id_to_rank[sid] = rank
            except (ValueError, TypeError):
                continue
        return scene_id_to_rank

    def extract_scene_id(self, source_uri: str) -> Optional[int]:
        if not source_uri or not isinstance(source_uri, str):
            return None
        # 匹配 _scene_{数字}_enhanced.txt
        match = re.search(r"_scene_(\d+)_enhanced\.txt", source_uri)
        return int(match.group(1)) if match else None

    def enhance(self, retrieved_chunks: List[Any], config: Dict[str, Any], asset_id: str) -> str:
        """
        执行上下文增强流程。
        """
        self.logger.info(">>> ContextEnhancer: Starting enhancement...")

        if not retrieved_chunks:
            # [防御] 如果传入空列表，直接返回空字符串或提示
            self.logger.warning("ContextEnhancer received empty chunks list.")
            return "(No RAG chunks provided.)"

        # --- 1. 提取 ID 并去重 (Extract & Deduplicate) ---
        hit_scene_ids = set()
        for chunk in retrieved_chunks:
            # [防御] 安全获取 URI，防止 chunk 是奇怪的对象
            # 优先尝试 source_uri (新版 SDK)，其次 source_ref (旧版)，最后 text (虽然不太可能)
            uri = getattr(chunk, 'source_uri', getattr(chunk, 'source_ref', None))

            # 如果 chunk 连 uri 都没有，记录日志并跳过
            if not uri:
                # 尝试打印 chunk 类型以便调试
                self.logger.debug(f"Skipping chunk with no URI attributes. Type: {type(chunk)}")
                continue

            sid = self.extract_scene_id(str(uri))
            if sid:
                hit_scene_ids.add(sid)
            else:
                self.logger.debug(f"Unparsable URI in chunk: {uri}")

        if not hit_scene_ids:
            self.logger.warning("No valid Scene IDs extracted from all retrieved chunks.")
            return "(No valid scenes identified from retrieval results.)"

        # --- 2. 范围过滤 (Scope Filtering) ---
        scope = config.get("control_params", {}).get("scope", {})
        valid_scene_ids = []

        if scope.get("type") == "episode_range":
            start_ep, end_ep = scope.get("value", [1, 9999])
            # 防御：确保 value 是列表且长度为 2
            if not isinstance(scope.get("value"), list) or len(scope.get("value")) != 2:
                start_ep, end_ep = 1, 9999

            self.logger.info(f"Applying scope filter: Episode {start_ep}-{end_ep}")

            for sid in hit_scene_ids:
                scene_data = self.scenes_map.get(str(sid))
                if scene_data:
                    # 防御：chapter_id 可能是 None
                    chapter_id = scene_data.get("chapter_id", 0)
                    if start_ep <= chapter_id <= end_ep:
                        valid_scene_ids.append(sid)
        else:
            valid_scene_ids = list(hit_scene_ids)

        if not valid_scene_ids:
            self.logger.warning("All scenes were filtered out by scope constraints.")
            return "(All retrieved scenes were outside the specified scope.)"

        # --- 3. 时序排序 (Timeline Sorting) ---
        sorted_ids = sorted(valid_scene_ids, key=lambda x: self.timeline_map.get(x, 99999))
        self.logger.info(f"Final scene sequence: {sorted_ids}")

        # --- 4. 内容重组 (Reconstruction) ---
        final_context_parts = []
        lang = config.get("lang", "zh")

        for sid in sorted_ids:
            scene_data = self.scenes_map.get(str(sid))
            if not scene_data:
                continue

            try:
                # 使用 Pydantic 模型生成文本 (复用已有的健壮逻辑)
                scene_obj = Scene(**scene_data)
                rich_text = scene_obj.to_rag_text(asset_id=asset_id, lang=lang)
                final_context_parts.append(rich_text)
                final_context_parts.append("\n" + "=" * 30 + "\n")
            except Exception as e:
                # 单个场景重组失败不应阻塞整个流程
                self.logger.error(f"Failed to reconstruct Scene {sid}: {e}")

        if not final_context_parts:
            return "(Failed to reconstruct any scene content.)"

        return "\n".join(final_context_parts)