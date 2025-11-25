
# 智能知识库部署引擎 (RAG Deployer) 详细设计文档 
 
版本: 2.0 
最后更新: 2025-11-23 
状态: 已发布 (Released) 
模块路径: ai_services/rag/ 
 
--- 
 
## 1. 概述 (Overview) 
 
### 1.1 背景与目标 
在 VSS Cloud 的 AI 叙事流中，RAG (Retrieval-Augmented Generation) 是连接原始剧本数据与下游生成任务（如解说词生成）的关键桥梁。 
RAG Deployer 的核心职责是将结构化的剧本蓝图（Blueprint）和散乱的人物事实（Facts）进行融合、清洗和富文本化，并将其安全、隔离地部署到 Google Vertex AI RAG 引擎中。 
 
### 1.2 核心能力 
* 数据融合 (Data Fusion): 自动将独立的人物属性/事实文件 (character_facts.json) 挂载到对应的剧本场景 (narrative_blueprint.json) 中，形成信息密度极高的“增强场景”。 
* 富文本化 (Rich Text Conversion): 将 JSON 数据转化为对 LLM 友好的自然语言文本块（Metadata Block + Narrative + Dialogue），以提升 Embedding 的语义相关性。 
* 多租户隔离 (Multi-Tenancy): 基于不可变的 org_id (UUID) 和 asset_id (UUID) 构建物理隔离的存储路径和语料库资源，确保数据安全。 
* 幂等部署 (Idempotency): 支持重复部署，自动处理语料库的创建或更新逻辑。 
 
--- 
 
## 2. 系统架构 (Architecture) 
 
### 2.1 数据流图 (Data Flow) 
 
```mermaid 
graph TD
  InputBP[Blueprint JSON] --> Fusion[Data Fusion Engine] 
  InputFacts[Facts JSON] --> Fusion 
  
  subgraph "Stage 1: Local Processing" 
  Fusion -->|Merge| EnhancedScenes[Enhanced Scene Objects] 
  EnhancedScenes -->|Format (i18n)| RichTextFiles[Rich Text .txt Files] 
  RichTextFiles -->|Staging| LocalDir[Local Staging Dir] 
  end 
 
  subgraph "Stage 2: Cloud Storage" 
  LocalDir -->|Upload| GCS[Google Cloud Storage] 
  GCS -->|Path: rag-engine-source/{org_id}/{asset_id}/| GCS_Blob 
  end 
 
  subgraph "Stage 3: Vertex AI Indexing" 
  GCS_Blob -->|Import Files| RAG[Vertex AI RAG Engine] 
  RAG -->|Create/Update| Corpus[RAG Corpus: {asset_id}-{org_id}] 
  end 
``` 
 
### 2.2 模块清单 
 
| 模块文件 | 类名 | 职责描述 | 
| :--- | :--- | :--- | 
| deployer.py | RagDeployer | 核心部署器。编排从文件加载、融合、上传到触发 RAG 索引的全流程。 | 
| schemas.py | Scene, NarrativeBlueprint | 数据契约与格式化。定义数据的 Pydantic 模型，并负责 to_rag_text 的富文本渲染。 | 
| metadata/schemas.json | N/A | 多语言配置。定义富文本生成时使用的标签（如“场景ID”、“氛围”等）的翻译。 | 
 
--- 
 
## 3. 关键技术决策 
 
### 3.1 隔离策略 (Isolation Strategy) 
为彻底解决多租户环境下的数据混淆和命名冲突问题，系统放弃了基于 name 的标识方案，转而全面采用 UUID。 
 
* GCS 存储路径: gs://{bucket}/rag-engine-source/{org_id}/{asset_id}/ 
 * org_id: 租户的 Organization UUID。 
 * asset_id: 媒资资产的唯一 UUID。 
* RAG 语料库名称: {asset_id}-{org_id} 
 * 确保即使不同租户上传了同名剧本，在 Vertex AI 中也是两个完全独立的 Corpus 资源。 
 
### 3.2 富文本化策略 (Rich Text Strategy) 
RAG 引擎对 JSON 这种稀疏数据的检索效果往往不佳。本模块在上传前，会将结构化数据“扁平化”为带有语义标记的文本块。 
 
转换示例: 
```text 
--- Metadata Block --- 
Asset ID: ... 
Scene ID: 10 
Location: 咖啡店 
Mood: Tense 
--- Inferred Facts --- 
车小小的推理事实: 技能是“演技超群”。 
--- Dialogues --- 
- 车小小: 为了姐妹 雄起 
- 楚昊轩: 你不冷吗 
```

这种格式既保留了元数据的结构化特征，又利用自然语言提升了向量检索的准确度。 
 
--- 
 
## 4. 配置说明 
 
### 4.1 标签国际化 (metadata/schemas.json) 
 
支持根据任务的 lang 参数，动态生成中文或英文的 RAG 源文件。 
 
```json 
{ 
 "zh": { 
 "metadata_block_header": "--- 元数据块 ---", 
 "scene_id_label": "场景ID", 
 ... 
 } 
} 
``` 
 
---