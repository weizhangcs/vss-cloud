 # 智能解说词生成引擎 (Narration Generator V3) 详细设计文档
 
 版本: 3.0 (Unified)
 最后更新: 2025-11-27
 状态: 已发布 (Released)
 模块路径: ai_services/narration/
 
 ---
 
 ## 1. 概述 (Overview)
 
 ### 1.1 背景与目标
 在长视频解说或短视频切片场景中，单纯依靠 RAG（检索增强生成）往往面临“上下文碎片化”、“逻辑乱序”和“风格单一”等问题。
 Narration Generator V3 旨在通过引入 **“四段式编排架构” (Four-Stage Orchestration)** 和 **“配置优先 (Config-First)”** 的设计思想，将非结构化的剧情素材转化为高质量、风格化且严格符合声画时长的视频解说文案。
 
 ### 1.2 核心能力
 * **有目的检索**: 基于用户意图（情感/悬疑/搞事业）动态构建查询，而非千篇一律的通用摘要。
 * **时序增强**: 利用本地人工标注的蓝图数据，强制纠正 RAG 返回的乱序片段，根除逻辑幻觉。
 * **风格化生成**: 支持动态注入人设（如“毒舌博主”、“深情电台”），并支持第一人称沉浸式解说。
 * **策略化校验**: 不仅校验声画物理时长，还引入 `overflow_tolerance` 策略，支持“强制留白”或“激进混剪”模式。
 * **强类型契约**: 引入 Pydantic Schema，在入口处对所有参数进行严格校验，拒绝“垃圾进，空气出”。
 
 ---
 
 ## 2. 系统架构 (Architecture)
 
 ### 2.1 模块架构图 (Base-Subclass Pattern)
 
 V3 版本采用了 **模板方法模式**，将通用的 RAG 流水线逻辑下沉至基类，业务逻辑保留在子类。
 
 ```mermaid
 classDiagram
     class BaseRagGenerator {
         <<Abstract>>
         +execute(config)
         #_validate_config()
         #_retrieve_from_rag()
         #_post_process()
     }
     class NarrationGeneratorV3 {
         +execute()
         #_construct_prompt()
         #_validate_and_refine()
     }
     class ContextEnhancer {
         +enhance()
     }
     class NarrationValidator {
         +validate_snippet()
     }
     
     BaseRagGenerator <|-- NarrationGeneratorV3
     NarrationGeneratorV3 --> ContextEnhancer : Use
     NarrationGeneratorV3 --> NarrationValidator : Use
 ```
 
 ### 2.2 数据流图 (Data Flow Pipeline)
 
 ```mermaid
 graph TD
   InputJSON["API Payload (Config)"] --> Step1
   LocalBlueprint["本地蓝图 (Narrative Blueprint)"] --> Step3
   LocalBlueprint --> Step5
 
   subgraph "Stage 1: Input Validation"
     Step1["Schema Validator"] -->|强类型对象| ServiceConfig["NarrationServiceConfig"]
   end
 
   subgraph "Stage 2: Intent-Based Retrieval"
     ServiceConfig --> Step2["Query Builder"]
     Step2 -->|生成语义查询| RAG["Vertex AI RAG Engine"]
     RAG -->|返回碎片化片段| RawChunks["Raw Context Chunks"]
   end
 
   subgraph "Stage 3: Context Enhancement"
     RawChunks --> Step3["Context Enhancer"]
     Step3 -->|清洗/排序/重组| EnhancedContext["完整有序上下文"]
   end
 
   subgraph "Stage 4: Synthesis"
     EnhancedContext --> Step4["Prompt Assembler"]
     ServiceConfig --> Step4
     Step4 -->|Full Prompt| LLM["Gemini 2.5"]
     LLM -->|初稿| InitialScript["初始解说词"]
   end
 
   subgraph "Stage 5: Strategic Validation"
     InitialScript --> Step5["Validator"]
     Step5 -->|计算时长策略| Check{Audio > Limit?}
     Check -- No --> FinalOutput
     Check -- Yes --> RefineLoop["Refinement Loop"]
     RefineLoop -->|缩写指令| LLM
     LLM -->|重写后文本| Step5
   end
 ```
 
 ---
 
 ## 3. 详细设计 (Detailed Design)
 
 ### 3.1 Stage 1: 配置校验 (Input Validation)
 * **输入**: Dict (来自 API Payload)
 * **逻辑**: 使用 Pydantic 的 `NarrationServiceConfig` 进行校验。
 * **Config-First**: `asset_name` 等上下文信息被注入 Config 对象，随流程流转，不再作为散乱参数传递。
 
 ### 3.2 Stage 2: 有目的检索 (Query Builder)
 * **核心**: 根据 `narrative_focus` 加载模版。
 * **Custom 支持**: 若 `narrative_focus="custom"`，直接使用用户传入的 `custom_prompts`，支持完全自定义的检索意图。
 
 ### 3.3 Stage 3: 本地时序增强 (Context Enhancer)
 * **溯源 (Tracing)**: 解析 RAG 返回的 GCS URI 提取 Scene ID。
 * **排序 (Sorting)**: 强制利用本地蓝图的 Timeline 纠正 RAG 的乱序。
 * **重组 (Reconstruction)**: 丢弃 RAG 的文本碎片，加载本地 Scene 完整数据，消除幻觉。
 
 ### 3.4 Stage 4: 风格化合成 (Synthesis)
 * **Prompt 组装**:
     * **风格/视角**: 从 `prompt_definitions.json` 加载。
     * **时长翻译**: 自动将业务层面的“3分钟”需求，换算为技术层面的“约X字”指令（`Minutes * 60 * SpeakingRate`），消除 LLM 对时间概念的模糊性。
 * **防御性清洗**: 内置正则清洗器 `_sanitize_text`，自动剔除 LLM 输出的舞台指示（如 `（音乐起）`），保护下游 TTS。
 
 ### 3.5 Stage 5: 策略化校验 (Strategic Validator)
 * **传统校验**: 仅比对 `Audio Duration > Visual Duration`。
 * **策略化校验 (V3)**: 引入 `overflow_tolerance` 比例系数。
     * **公式**: `Limit = Visual * (1 + tolerance)`
     * **场景 A (解说)**: 设为 `-0.15`，强制预留 15% 画面空隙。
     * **场景 B (混剪)**: 设为 `+0.20`，允许音频稍长，后续填充 B-Roll。
 
 ---
 
 ## 4. 配置说明 (Configuration)
 
 ### 4.1 配置文件结构 (ai_services/narration/metadata/)
 
 * **query_templates.json**: 
     * 用途: RAG 检索阶段。
     * 内容: 定义 narrative_focus 到 Query 的映射。
 * **prompt_definitions.json**: 
     * 用途: LLM 生成阶段。
     * 内容: 聚合了 `styles`, `perspectives`, `constraints` (时长/字数模版)。
 
 ### 4.2 Prompt 模板 (ai_services/narration/prompts/)
 
 * `narration_generator_{lang}.txt`: 核心生成模版。
 * `narration_refine_{lang}.txt`: 缩写修正模版。
 
 ---
 
 ## 5. 成本与可观测性
 
 * **计费 (Billing)**: 集成 `CostCalculator`，基于 Vertex AI 最新定价（Pro/Flash 分层）计算每次调用的 USD/RMB 成本。
 * **日志**: 关键节点（Prompt构建、Refine触发、校验结果）均有 INFO/WARN 级别日志，支持全链路追踪。