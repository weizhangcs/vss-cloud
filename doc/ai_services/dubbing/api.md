
# VSS Cloud API 集成指南 (Unified) 
 
适用版本: v1.2 (Narration V2 + Dubbing V2) 
最后更新: 2025-11-21 
 
--- 
 
## 1. 任务流程概览 
 
云端任务通常按以下顺序链式执行： 
1. 生成解说词 (GENERATE_NARRATION): 将蓝图数据转化为文本脚本。 
2. 生成配音 (GENERATE_DUBBING): 将文本脚本转化为音频文件。 
3. 生成剪辑脚本 (GENERATE_EDITING_SCRIPT): 基于音频时长匹配画面。 
 
所有任务创建均使用端点: POST /api/v1/tasks/ 
 
--- 
 
## 2. 任务详解 
 
### 2.1 生成解说词 (Generate Narration) 
 
利用 RAG 和 LLM 生成风格化的解说文案。 
 
Task Type: GENERATE_NARRATION 
 
Payload 示例: 
```json 
{ 
 "task_type": "GENERATE_NARRATION", 
 "payload": { 
 "series_id": "series_001", 
 "series_name": "总裁的契约女友", 
 "blueprint_path": "resources/projects/series_001/narrative_blueprint.json", 
 "output_path": "outputs/narrations/script_v1.json", 
 "service_params": { 
 "control_params": { 
 "narrative_focus": "romantic_progression", // 叙事焦点 
 "scope": { "type": "episode_range", "value": [1, 5] }, // 剧情范围 
 "style": "humorous", // [关键] 风格将传递给配音环节 
 "target_duration_minutes": 5 
 } 
 } 
 } 
} 
```
 
 
### 2.2 生成配音 (Generate Dubbing) 
 
利用 AI TTS 引擎（如 CosyVoice）将解说词转换为语音。 
 
Task Type: GENERATE_DUBBING 
 
Payload 结构: 
 
| 字段 | 说明 | 示例 | 
| :--- | :--- | :--- | 
| input_narration_path | [必填] 上一步生成的解说词 JSON 文件路径（相对路径）。 | outputs/narrations/script_v1.json | 
| output_path | [必填] 配音结果 JSON 的保存路径。 | outputs/dubbing/audio_meta.json | 
| service_params | 配音参数配置对象。 | 见下文 | 
 
Service Params 详解: 
 
| 参数名 | 类型 | 说明 | 
| :--- | :--- | :--- | 
| template_name | String | [必填] 指定使用的配音模板（在 Cloud 端配置）。


推荐: chinese_paieas_replication | 
| style | String | [可选] 强制指定配音风格（humorous/emotional/suspense）。


如果不填，默认自动继承 Narration 任务中的 style 配置。 | 
| speed | Float | [可选] 语速控制。1.0 为标准，1.2 为快，0.8 为慢。 | 
| instruct | String | [高级] 手动覆盖 TTS 的 Prompt 指令。


例如: "用极度夸张的语气说<|endofprompt|>" | 
 
Payload 示例: 
 
```json 
{ 
 "task_type": "GENERATE_DUBBING", 
 "payload": { 
 "input_narration_path": "outputs/narrations/script_v1.json", 
 "output_path": "outputs/dubbing/audio_meta.json", 
 "service_params": { 
 "template_name": "chinese_paieas_replication", 
 "speed": 1.1, 
 // style 字段留空，则自动使用解说词中的风格（如 humorous） 
 } 
 } 
} 
``` 
 
输出产物: 
任务完成后，Cloud 端会生成： 
1. 音频文件: 位于 shared_media/tmp/dubbing_task_{id}_audio/ 目录下的一系列 .wav 或 .mp3 文件。 
2. 结果 JSON: output_path 指定的文件，包含每个片段的文本、时长 (duration_seconds) 和音频文件路径 (audio_file_path)。 
 
--- 
 
## 3. 错误排查 
 
如果任务状态为 FAILED，请检查 Result 中的 error 字段。 
 
* Error: Validation Failed (Audio > Visual): (Narration阶段) 说明解说词太长，画面不够。V2 引擎会自动尝试缩写，但如果极其不匹配仍可能报错。 
* Error: FFmpeg merge failed: (Dubbing阶段) 说明音频拼接失败，通常是临时文件 IO 问题。 
* Error: Upload reference audio failed: (Dubbing阶段) 说明 replication_source 指定的参考音频文件在服务器上不存在。