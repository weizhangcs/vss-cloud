# VSS Cloud API 参考文档：智能解说词生成服务 
 
服务模块: Narration Generator V2 
适用版本: v1.2.0+ 
更新日期: 2025-11-20 
 
--- 
 
## 1. 接口概览 
 
本接口用于触发智能解说词生成任务。V2 版本引入了“四段式编排引擎”（Query-Enhance-Synthesize-Refine），支持基于意图的检索、风格化生成以及声画时长的自动校验与缩写。 
 
* API 端点: POST /api/v1/tasks/ 
* 认证方式: Header 需包含 X-Instance-ID 和 X-Api-Key 
 
--- 
 
## 2. 请求结构 (Request Payload) 
 
要创建一个解说词生成任务，需将 task_type 设置为 GENERATE_NARRATION，并构造如下 payload： 
 
```json 
{ 
 "task_type": "GENERATE_NARRATION", 
 "payload": { 
 // --- [基础定位信息] --- 
 "asset_name": "总裁的契约女友", // [修正] 媒资展示名称（用于 Prompt 组装） 
 "asset_id": "98995bd5-d27e-45fe-ae62-ee46c31a84b4", // [修正] 媒资唯一 ID（用于定位 RAG 语料库）,建议采用uuid,但不强制 
 
 // --- [资源路径] (必填) --- 
 // 本蓝图文件的相对路径。通常由 Edge 端上传至 tmp/ 目录。
 "blueprint_path": "tmp/ABCDEFGH_narrative_blueprint.json",
 
 // --- [核心控制参数] (V2 引擎配置) --- 
 "service_params": { 
 "lang": "zh", // 语言 (zh/en), 默认 zh 
 "model": "gemini-2.5-flash", // 模型选择 (可选) 
 "rag_top_k": 50, // RAG 检索片段数量 (建议 50-100) 
 "speaking_rate": 4.2, // [可选] 语速校验标准 (字/秒), 默认 4.2 
 
 // >>> 创作控制参数 (Control Parameters) <<< 
 "control_params": { 
 // 1. 叙事焦点 (决定 RAG 检索什么内容) 
 "narrative_focus": "general", 
 
 // 2. 剧情范围 (决定使用哪些素材) 
 "scope": { 
 "type": "episode_range", // 范围类型 
 "value": [1, 5] // 范围值 (如第1集到第5集) 
 }, 
 
 // 3. 角色聚焦 (决定关注谁的戏份) 
 "character_focus": { 
 "mode": "specific", // 聚焦模式 
 "characters": ["车小小", "楚昊轩"] // 角色名列表 
 }, 
 
 // 4. 解说风格 (决定 LLM 的语气口吻) 
 "style": "objective", 
 
 // 5. 视角设定 (决定第一/第三人称) 
 "perspective": "third_person", 
 
 // 6. 目标时长控制 (分钟) - 软约束 
 "target_duration_minutes": 5 
 } 
 } 
 } 
} 
``` 
 
--- 
 
## 3. 参数详解 (Control Parameters) 
 
以下参数定义在 payload.service_params.control_params 中，直接控制生成器的行为。 
 
### 3.1 叙事焦点 (narrative_focus) 
 
| 可选值 (Key) | 说明 | 适用场景 | 
| :--- | :--- | :--- | 
| general | (默认) 通用全剧剧情 | 概览、大纲、速看 | 
| romantic_progression | 情感递进线 | CP 混剪、恋爱专题、甜虐解说 | 
| business_success | 搞事业/复仇线 | 大女主/龙王复仇、职场逆袭 | 
| suspense_reveal | 悬疑揭秘线 | 悬疑剧、反转盘点、细节分析 | 
| character_growth | 个人成长弧光 | 人物传记、深度角色剖析 | 
 
### 3.2 剧情范围 (scope) 
 
V2 引擎利用本地 blueprint_path 中的数据进行精确过滤。 
 
| 类型 (type) | 值 (value) | 说明 | 
| :--- | :--- | :--- | 
| full | (无) | (默认) 全剧范围。 | 
| episode_range | [start, end] | 推荐。仅保留指定集数范围内的场景（闭区间）。例如 [1, 10]。 | 
 
### 3.3 角色聚焦 (character_focus) 
 
| 模式 (mode) | 角色列表 (characters) | 说明 | 
| :--- | :--- | :--- | 
| all | (忽略) | (默认) 关注所有主要角色。 | 
| specific | ["角色A", "角色B"] | 强指令。Query 会显式要求提取这些角色的互动。 | 
 
### 3.4 解说风格 (style) 
 
| 可选值 (Key) | 风格描述 | 
| :--- | :--- | 
| objective | (默认) 客观冷静，纪录片风。 | 
| humorous | 幽默吐槽，短视频风。 | 
| emotional | 深情细腻，电台情感风。 | 
| suspense | 悬疑解密，层层剥茧。 | 
 
### 3.5 视角 (perspective) 
 
| 可选值 | 说明 | 额外参数 | 
| :--- | :--- | :--- | 
| third_person | (默认) 上帝视角。 | 无 | 
| first_person | 角色沉浸式自述。 | 需提供 perspective_character: "角色名" | 
 
### 3.6 目标时长 (target_duration_minutes) 
 
* 类型: int (分钟) 
* 作用: 
 1. Prompt 约束: 告知 AI 期望的篇幅。 
 2. 配合 Validation: 辅助系统进行字数预估。 
 
--- 
 
## 4. 自动校验与重写 (Validation & Refinement) 
 
系统内置了声画对位校验机制（Stage 4），无需额外配置。 
 
1. 校验逻辑: 系统计算每一段解说词的 预估朗读时长 (基于 speaking_rate) vs 对应画面的物理时长 (基于 source_scene_ids)。 
2. 自动重写: 如果 Audio Duration > Visual Duration，系统会自动触发 LLM 进行 缩写 (Refinement)，并强制保持原有风格。 
3. 结果标记: 经过缩写的片段，在返回的 JSON metadata 中会包含 refined: true 标记。 
 
--- 
 
## 5. 调用配方 (Recipes) 
 
### 配方 A：5分钟全剧速看 (标准长视频) 
```json 
"control_params": { 
 "narrative_focus": "general", 
 "scope": { "type": "episode_range", "value": [1, 30] }, 
 "style": "objective", 
 "target_duration_minutes": 5 
} 
``` 
 
### 配方 B：1分钟甜宠短视频 (抖音/TikTok) 
```json 
"control_params": { 
 "narrative_focus": "romantic_progression", 
 "scope": { "type": "episode_range", "value": [1, 5] }, 
 "character_focus": { "mode": "specific", "characters": ["车小小", "楚昊轩"] }, 
 "style": "humorous", 
 "target_duration_minutes": 1 
} 
```
 
 
### 配方 C：角色第一人称自述 (人物志) 
```json 
"control_params": { 
 "narrative_focus": "character_growth", 
 "scope": { "type": "full" }, 
 "character_focus": { "mode": "specific", "characters": ["车小小"] }, 
 "style": "emotional", 
 "perspective": "first_person", 
 "perspective_character": "车小小" 
} 
```