# tests/run_narration_generator_v2_stage2.py
# 描述: [Stage 2] 本地时序增强 - 验证 "文件名溯源" 与 "逻辑重组"
# 运行方式: python tests/run_narration_generator_v2_stage2.py

import sys
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

# 将项目根目录添加到Python路径中
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

# 导入引导程序
from tests.lib.bootstrap import bootstrap_local_env_and_logger
# 导入 Schema 用于生成标准化的 RAG 文本格式
from ai_services.ai_platform.rag.schemas import Scene, load_i18n_strings


class MockRagContext:
    """模拟 RAG 返回的 Context 对象"""

    def __init__(self, source_uri: str, distance: float, text_snippet: str):
        self.source_uri = source_uri
        self.distance = distance
        self.text = text_snippet


class ContextEnhancer:
    """
    [核心业务逻辑]
    负责清洗 RAG 返回的碎片，结合本地蓝图，重组为有序、完整的上下文。
    """

    def __init__(self, blueprint_path: Path, logger: logging.Logger):
        self.logger = logger
        self.blueprint_data = self._load_blueprint(blueprint_path)

        # 预处理：建立 narrative_index 到 scene_id 的映射，以及 scene_id 到 Scene 对象的映射
        self.timeline_map = self._build_timeline_map()
        self.scenes_map = self.blueprint_data.get("scenes", {})

        # 加载 i18n 字符串，以便调用 Scene.to_rag_text
        # 注意：这里假设 localization 文件在标准位置，或者我们需要 mock 它
        # 为简化测试，我们暂时不加载真实 i18n，Scene.to_rag_text 会回退到默认英文或硬编码
        # 在生产代码中，这里需要注入 localization_path

    def _load_blueprint(self, path: Path) -> Dict:
        self.logger.info(f"正在加载剧本蓝图: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _build_timeline_map(self) -> Dict[int, int]:
        """构建 {narrative_index: scene_id} 的有序映射"""
        timeline = self.blueprint_data.get("narrative_timeline", {}).get("sequence", {})
        # 这里的结构是 "1": {"narrative_index": 1}, 假设 key 就是顺序
        # 实际上我们需要确认 narrative_index 的含义。
        # 根据您提供的数据："1": {"narrative_index": 1}，且描述说 index 对应 Scene ID?
        # 通常 narrative_timeline 是一个有序列表。
        # 让我们假设 sequence 的 key 是排序用的索引，value 里的 narrative_index 也是某种顺序
        # 或者更简单：我们直接信任 sequence 的 key 顺序，去查对应的 scene_id
        # 修正：查看您的数据，narrative_timeline.sequence 的 key 是字符串 "1", "2"...
        # 让我们建立一个从 scene_id 到 sort_order 的反向映射，用于排序

        scene_id_to_rank = {}
        sequence = self.blueprint_data.get("narrative_timeline", {}).get("sequence", {})

        # 假设 sequence 的 key 就是场景 ID (从数据看 "1": {"narrative_index": 1}，场景ID也是1)
        # 我们用 narrative_index 作为排序依据
        for key, val in sequence.items():
            # 这里假设 key 对应 scene_id (string), narrative_index 是排序权重
            try:
                sid = int(key)
                rank = val.get("narrative_index", 0)
                scene_id_to_rank[sid] = rank
            except:
                continue
        return scene_id_to_rank

    def extract_scene_id(self, source_uri: str) -> int:
        """
        关键逻辑：从 GCS URI 中提取 Scene ID。
        URI 样例: .../总裁的契约女友_v3_scene_16_enhanced.txt
        """
        # 正则匹配 _scene_{数字}_enhanced.txt
        match = re.search(r"_scene_(\d+)_enhanced\.txt", source_uri)
        if match:
            return int(match.group(1))
        return None

    def enhance(self, retrieved_chunks: List[MockRagContext], config: Dict) -> str:
        """
        执行增强流程：提取ID -> 去重 -> 范围过滤 -> 排序 -> 重组
        """
        self.logger.info(">>> 开始执行 Context 增强流程...")

        # 1. 提取 ID 并去重
        hit_scene_ids = set()
        for chunk in retrieved_chunks:
            sid = self.extract_scene_id(chunk.source_uri)
            if sid:
                hit_scene_ids.add(sid)
            else:
                self.logger.warning(f"无法解析 URI: {chunk.source_uri}")

        self.logger.info(f"RAG 命中场景 ID (去重后): {hit_scene_ids}")

        # 2. 范围过滤 (Scope Filtering)
        scope = config.get("control_params", {}).get("scope", {})
        valid_scene_ids = []

        if scope.get("type") == "episode_range":
            start_ep, end_ep = scope.get("value", [1, 100])
            self.logger.info(f"应用范围过滤: 第 {start_ep}-{end_ep} 集")

            # 遍历命中的场景，检查其 chapter_id (集数)
            for sid in hit_scene_ids:
                scene_data = self.scenes_map.get(str(sid))  # JSON key 是 str
                if not scene_data: continue

                chapter_id = scene_data.get("chapter_id")
                if start_ep <= chapter_id <= end_ep:
                    valid_scene_ids.append(sid)
                else:
                    self.logger.info(f"场景 {sid} (第 {chapter_id} 集) 超出范围，已剔除。")
        else:
            valid_scene_ids = list(hit_scene_ids)

        # 3. 时序排序 (Timeline Sorting)
        # 使用预构建的 rank map 进行排序
        sorted_ids = sorted(valid_scene_ids, key=lambda x: self.timeline_map.get(x, 9999))
        self.logger.info(f"最终选定并排序的场景流: {sorted_ids}")

        # 4. 内容重组 (Reconstruction)
        final_context_parts = []
        series_name = self.blueprint_data.get("project_metadata", {}).get("project_name", "Unknown")

        for sid in sorted_ids:
            scene_data = self.scenes_map.get(str(sid))
            # 将 dict 转换为 Pydantic Scene 对象，利用其 to_rag_text 方法生成标准文本
            # 注意：这里我们为了演示，先不处理 enhanced_facts，只处理基础信息
            scene_obj = Scene(**scene_data)

            # 生成高质量文本
            rich_text = scene_obj.to_rag_text(series_id=series_name, lang='zh')
            final_context_parts.append(rich_text)
            final_context_parts.append("\n" + "=" * 30 + "\n")  # 分隔符

        return "\n".join(final_context_parts)


def main():
    settings, logger = bootstrap_local_env_and_logger(project_root)

    rag_schema_path = project_root / "ai_services" / "ai_platform" / "rag" / "metadata" / "schemas.json"
    load_i18n_strings(rag_schema_path)

    # 1. 准备模拟数据
    # 这里我们要模拟 "碎片化" 和 "乱序" 的 RAG 返回结果
    # 假设 Query 是关于 "车小小和楚昊轩"，RAG 返回了以下片段（注意顺序是乱的，且有 Chunk #5 这种断头片段）
    mock_retrievals = [
        MockRagContext(
            source_uri="gs://bucket/path/总裁的契约女友_v3_scene_10_enhanced.txt",
            distance=0.36,
            text_snippet="...场景ID: 10..."
        ),
        MockRagContext(
            source_uri="gs://bucket/path/总裁的契约女友_v3_scene_53_enhanced.txt",
            distance=0.37,
            text_snippet="...场景ID: 53..."
        ),
        # 这个模拟刚才那个断头的 Chunk #5，内容不完整，但 URI 指向 Scene 16
        MockRagContext(
            source_uri="gs://bucket/path/总裁的契约女友_v3_scene_16_enhanced.txt",
            distance=0.38,
            text_snippet="- 陆乘风: 宋安娜呢？ - 车小小: 你认错人了..."
        ),
        # 一个超出范围的场景 (假设我们要过滤掉第20集以后的)
        MockRagContext(
            source_uri="gs://bucket/path/总裁的契约女友_v3_scene_32_enhanced.txt",
            distance=0.39,
            text_snippet="...场景ID: 32..."
        )
    ]

    # 2. 准备配置 (只看前 15 集)
    config = {
        "control_params": {
            "scope": {"type": "episode_range", "value": [1, 15]}
        }
    }

    # 3. 实例化 Enhancer
    # 指向您的本地蓝图文件
    blueprint_path = project_root / "tests/testdata/narrative_blueprint_28099a52_KRe4vd0.json"
    enhancer = ContextEnhancer(blueprint_path, logger)

    # 4. 执行增强
    final_context = enhancer.enhance(mock_retrievals, config)

    # 5. 验证输出
    print("\n" + "#" * 20 + " 最终生成的 Prompt 上下文 " + "#" * 20 + "\n")
    print(final_context)

    # 验证点检查
    print("\n" + "#" * 20 + " 验证检查点 " + "#" * 20)
    if "场景ID: 10" in final_context:
        print("✅ [包含] Scene 10 (正常范围内)")
    if "场景ID: 16" in final_context:
        print("✅ [修复] Scene 16 (从碎片中成功恢复了完整内容)")
        if "你认错人了" in final_context and "剧集ID" in final_context:  # 检查是否包含了完整头部和对话
            print("   - 确认: 包含了元数据头和完整对话")
    if "场景ID: 53" not in final_context:  # Scene 53 是第 25 章，应该被过滤
        print("✅ [过滤] Scene 53 (超出第 15 集范围，已正确剔除)")
    else:
        print("❌ [失败] Scene 53 未被剔除 (Check Chapter ID mapping)")

    # 检查顺序 (10 -> 16 -> 32)
    idx_10 = final_context.find("场景ID: 10")
    idx_16 = final_context.find("场景ID: 16")
    idx_32 = final_context.find("场景ID: 32")

    if idx_10 < idx_16 < idx_32:
        print("✅ [排序] 场景顺序正确 (10 -> 16 -> 32)")
    else:
        print(f"❌ [失败] 场景顺序错误: indices {idx_10}, {idx_16}, {idx_32}")


if __name__ == "__main__":
    main()