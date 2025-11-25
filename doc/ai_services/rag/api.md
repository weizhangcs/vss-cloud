
# VSS Cloud API 参考文档：RAG 部署服务 
 
服务模块: RAG Deployer 
适用版本: v2.0+ 
更新日期: 2025-11-23 
 
--- 
 
## 1. 接口概览 
 
本接口用于初始化或更新一个项目的 RAG 知识库。它是所有下游 AI 生成任务（如解说词生成）的前置依赖。 
该任务会将上传到临时区的原始 JSON 文件处理后存入 GCS 归档，并建立 Vertex AI 索引。 
 
* API 端点: POST /api/v1/tasks/ 
* 认证方式: Header 需包含 X-Instance-ID 和 X-Api-Key 
 
--- 
 
## 2. 请求结构 (Request Payload) 
 
要创建一个 RAG 部署任务，需将 task_type 设置为 DEPLOY_RAG_CORPUS。 
 
```json 
{ 
 "task_type": "DEPLOY_RAG_CORPUS", 
 "payload": { 
 // --- [资产标识] --- 
 // [必填] 媒资资产的唯一标识符 (UUID)，用于隔离语料库 
 "asset_id": "20251123-xxxx-xxxx-xxxx-xxxxxxxxxxxx", 
 
 // --- [输入文件路径] --- 
 // 请传入相对于 shared_media 根目录的路径 
 // 1. 剧本蓝图 (包含场景、对话、基础元数据) 
 "blueprint_input_path": "tmp/narrative_blueprint_v3.json", 
 
 // 2. 增强事实 (包含 Character Pipeline 产出的分析结果) 
 "facts_input_path": "tmp/character_facts_v3.json", 
 
 // --- [可选参数] --- 
 // "series_id": "..." (已废弃，请使用 asset_id) 
 } 
} 
``` 
 
--- 
 
## 3. 参数详解 
 
| 字段名 | 类型 | 必填 | 说明 | 
| :--- | :--- | :--- | :--- | 
| asset_id | UUID/String | 是 | 核心标识符。决定了生成的 RAG Corpus 名称 ({asset_id}-{org_id}) 以及 GCS 上的存储路径。请确保同一项目的多次部署使用相同的 ID 以支持覆盖更新。 | 
| blueprint_input_path | String | 是 | 剧本蓝图文件的相对路径。通常由 Edge 端上传至 tmp/ 目录。 | 
| facts_input_path | String | 是 | 人物事实文件的相对路径。通常是 CHARACTER_PIPELINE 任务的产出物。 | 
 
--- 
 
## 4. 输出产物 & 状态 
 
任务完成后，Task.result 将包含以下关键信息： 
 
```json 
{ 
 "message": "RAG deployment process initiated successfully.", 
 "corpus_name": "20251123-...-...", // 生成的 RAG 语料库资源名称 
 "source_gcs_uri": "gs://bucket/rag-engine-source/org_id/asset_id", // GCS 存储位置 
 "total_scene_count": 63 
} 
``` 
 
### 注意事项 
 
* 异步索引: 任务返回 Completed 仅代表“文件已处理并上传，且导入请求已发送给 Google”。Vertex AI 在后台进行索引可能还需要数分钟。请稍作等待再发起 GENERATE_NARRATION 请求。 
* 覆盖更新: 如果使用相同的 asset_id 再次提交，系统会自动检测到现有的 Corpus 并执行增量更新（Import Files），不会重复创建语料库。