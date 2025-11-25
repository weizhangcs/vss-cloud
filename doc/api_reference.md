
# VSS Cloud API 集成指南 (Unified) 
 
适用版本: v1.2.1 (Narration V2 + Dubbing V2) 
最后更新: 2025-11-21 
 
--- 
 
## 1. 任务流程概览 
 
云端任务通常按以下顺序链式执行： 
1. RAG 部署 (DEPLOY_RAG_CORPUS): (前置) 将剧本和事实数据部署到知识库。 
2. 生成解说词 (GENERATE_NARRATION): 基于 RAG 和 LLM 生成风格化文案。 
3. 生成配音 (GENERATE_DUBBING): 将文本脚本转化为音频文件。 
 
所有任务创建均使用端点: POST /api/v1/tasks/ 
 
--- 
 
## 2. 任务详解 
 
### 2.1 部署 RAG 知识库 (Deploy RAG Corpus) 
 
初始化或更新项目的知识库，这是生成解说词的前置条件。 
 
Task Type: DEPLOY_RAG_CORPUS 
 
Payload 示例: 
json 
{ 
 "task_type": "DEPLOY_RAG_CORPUS", 
 "payload": { 
 "asset_id": "20251123-xxxx-xxxx", // [必填] 媒资唯一标识 (UUID) 
 // 输入文件路径 (通常由 Edge 端上传至 tmp/ 目录) 
 "blueprint_input_path": "tmp/narrative_blueprint.json", 
 "facts_input_path": "tmp/character_facts.json" 
 } 
} 
 
 
--- 
 
### 2.2 生成解说词 (Generate Narration) 
 
利用 RAG 和 LLM 生成风格化的解说文案。 
 
Task Type: GENERATE_NARRATION 
 
Payload 示例: 
json 
{ 
 "task_type": "GENERATE_NARRATION", 
 "payload": { 
 // --- [基础定位信息] --- 
 "asset_name": "总裁的契约女友", // 媒资展示名称（用于 Prompt 组装） 
 "asset_id": "20251123-xxxx-xxxx", // 媒资唯一 ID（用于定位 RAG 语料库） 
 
 // --- [资源路径] (必填) --- 
 // 剧本蓝图文件的相对路径。通常由 Edge 端上传至 tmp/ 目录。 
 "blueprint_path": "tmp/narrative_blueprint.json", 
 
 // --- [核心控制参数] (V2 引擎配置) --- 
 "service_params": { 
 "lang": "zh", // 语言 (zh/en), 默认 zh 
 "model": "gemini-2.5-flash", // 模型选择 (可选) 
 "rag_top_k": 50, // RAG 检索片段数量 
 
 // >>> 创作控制参数 (Control Parameters) <<< 
 "control_params": { 
 "narrative_focus": "romantic_progression", // 叙事焦点 
 "scope": { 
 "type": "episode_range", 
 "value": [1, 5] // 剧情范围 
 }, 
 "character_focus": { 
 "mode": "specific", 
 "characters": ["车小小", "楚昊轩"] 
 }, 
 "style": "humorous", // 解说风格 
 "perspective": "third_person", // 视角设定 
 "target_duration_minutes": 5 // 目标时长 (软约束) 
 } 
 } 
 } 
} 
 
 
--- 
 
### 2.3 生成配音 (Generate Dubbing) 
 
利用 AI TTS 引擎（如 CosyVoice）将解说词转换为语音。 
 
Task Type: GENERATE_DUBBING 
 
Payload 结构: 
 
| 字段 | 说明 | 示例 | 
| :--- | :--- | :--- | 
| input_narration_path | [必填] 上一步生成的解说词 JSON 文件路径（相对路径）。 | tmp/narration_script_result.json | 
| output_path | [必填] 配音结果 JSON 的保存路径。 | tmp/dubbing_result.json | 
| service_params | 配音参数配置对象。 | 见下文 | 
 
Service Params 详解: 
 
| 参数名 | 类型 | 说明 | 
| :--- | :--- | :--- | 
| template_name | String | [必填] 指定使用的配音模板。


推荐: chinese_paieas_replication | 
| style | String | [可选] 强制指定配音风格（humorous/emotional/suspense）。


如果不填，默认自动继承 Narration 任务中的 style 配置。 | 
| speed | Float | [可选] 语速控制。1.0 为标准。 | 
| instruct | String | [高级] 手动覆盖 TTS 的 Prompt 指令。


例如: "用极度夸张的语气说<|endofprompt|>" | 
 
Payload 示例: 
 
json 
{ 
 "task_type": "GENERATE_DUBBING", 
 "payload": { 
 "input_narration_path": "tmp/narration_script_v1.json", 
 "output_path": "tmp/dubbing_result.json", 
 "service_params": { 
 "template_name": "chinese_paieas_replication", 
 "speed": 1.1 
 } 
 } 
} 
 
 
输出产物: 
任务完成后，Cloud 端会生成： 
1. 音频文件: 位于 tmp/dubbing_task_{id}_audio/ 目录下的一系列 .wav 或 .mp3 文件。 
2. 结果 JSON: output_path 指定的文件，包含每个片段的文本、时长 (duration_seconds) 和音频文件下载路径 (audio_file_path)。 
 
--- 
 
## 3. 错误排查 
 
如果任务状态为 FAILED，请检查 Result 中的 error 字段。 
 
* Validation Failed: (Narration阶段) 解说词太长，画面不够。V2 引擎会自动缩写，但如果极端不匹配仍可能报错。 
* Payload missing keys: 检查 asset_id 或 asset_name 是否正确传递。 
* FFmpeg merge failed: (Dubbing阶段) 音频拼接失败，通常是临时文件 IO 问题。 
* Upload reference audio failed: (Dubbing阶段) 参考音频文件丢失。