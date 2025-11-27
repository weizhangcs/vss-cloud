 # VSS Cloud API 参考文档：智能解说词生成服务
 
 服务模块: Narration Generator V3
 适用版本: v1.2.0-alpha.3+
 更新日期: 2025-11-27
 
 ---
 
 ## 1. 接口概览
 
 本接口用于触发智能解说词生成任务。V3 版本采用了 **Config-First 架构**，支持强类型参数校验、策略化时长控制以及完全自定义的提示词注入。
 
 * **API 端点**: `POST /api/v1/tasks/`
 * **认证方式**: Header 需包含 `X-Instance-ID` 和 `X-Api-Key`
 
 ---
 
 ## 2. 请求结构 (Request Payload)
 
 要创建一个解说词生成任务，需将 `task_type` 设置为 `GENERATE_NARRATION`。
 
 ### 完整示例
 
 ```json
 {
   "task_type": "GENERATE_NARRATION",
   "payload": {
     // --- [基础定位信息] ---
     "asset_name": "总裁的契约女友", // [必填] 媒资展示名称（用于 Prompt 组装）
     "asset_id": "a7407c6a-63fd-40c3-9a5b-ae810fae0a2c", // [必填] 媒资唯一 ID (UUID)
     
     // --- [资源路径] ---
     "blueprint_path": "tmp/narrative_blueprint.json", // [必填] 蓝图文件相对路径
     
     // --- [核心服务参数] ---
     "service_params": {
       "lang": "zh", // 语言: "zh" 或 "en"
       "model": "gemini-2.5-pro", // 模型选择
       "rag_top_k": 50, // 检索片段数 (建议 50-100)
       "speaking_rate": 4.2, // 语速标准 (字/秒)
       "overflow_tolerance": 0.0, // 时长策略控制杆 (见下文详解)
       "debug": true, // 是否开启调试日志
       
       // >>> 创作控制参数 <<<
       "control_params": {
         // 1. 叙事焦点
         "narrative_focus": "romantic_progression", 
         
         // 2. 剧情范围 (支持 "full" 或 "episode_range")
         "scope": {
           "type": "episode_range",
           "value": [1, 5]
         },
         
         // 3. 角色聚焦
         "character_focus": {
           "mode": "specific",
           "characters": ["车小小", "楚昊轩"]
         },
         
         // 4. 风格与视角
         "style": "emotional",
         "perspective": "first_person",
         "perspective_character": "车小小", // 第一人称时必填
         
         // 5. 目标时长 (分钟)
         "target_duration_minutes": 3,
         
         // 6. [高级] 自定义提示词 (仅当 style/focus 为 custom 时生效)
         "custom_prompts": {
             "narrative_focus": "深度挖掘{asset_name}中的...",
             "style": "你是一个..."
         }
       }
     }
   }
 }
 ```
 
 ---
 
 ## 3. 参数详解
 
 ### 3.1 核心策略参数 (Service Params)
 
 | 参数名 | 类型 | 默认值 | 说明 |
 | :--- | :--- | :--- | :--- |
 | **overflow_tolerance** | Float | `0.0` | **时长策略控制杆**（相对比例）。<br>• `0.0`: **严格对齐**。解说词朗读时长 $\le$ 画面物理时长。<br>• `-0.15`: **强制留白**。解说词时长 $\le$ 画面时长 $\times$ 85%（预留 15% 呼吸感）。<br>• `0.20`: **允许溢出**。解说词时长 $\le$ 画面时长 $\times$ 120%（适用于混剪/B-Roll填充）。 |
 | **speaking_rate** | Float | `4.2` | 语速标准 (字/秒)。数值越大，允许生成的字数越多。建议中文 `4.2`，英文 `2.5`。 |
 | **rag_top_k** | Int | `50` | RAG 检索的上下文片段数量。长剧建议 `50-100`。系统会自动根据总场景数取最小值 `min(top_k, total_scenes)`。 |
 
 ---
 
 ### 3.2 创作控制参数 (Control Params)
 
 #### **1. 视角设定 (Perspective)**
 
 | 可选值 (Key) | 说明 | 依赖字段 |
 | :--- | :--- | :--- |
 | **`third_person`** | (默认) **上帝视角**。客观、全知全能的叙述。 | 无 |
 | **`first_person`** | **角色第一人称**。沉浸式自述（“我...”）。 | 必须填写 `perspective_character` 指定角色名（如 "车小小"）。 |
 
 #### **2. 剧情范围 (Scope)**
 
 | 类型 (type) | 值 (value) | 说明 |
 | :--- | :--- | :--- |
 | **`full`** | `null` 或省略 | (默认) **全剧模式**。检索整部剧的所有剧情。 |
 | **`episode_range`** | `[start, end]` | **集数范围模式**。仅检索指定集数区间（闭区间）。例如 `[1, 5]` 表示第1到第5集。 |
 
 > 注：`scene_selection` 模式暂未开放。
 
 #### **3. 叙事焦点 (Narrative Focus)**
 
 决定 RAG 检索什么内容以及 LLM 讲什么故事。
 
 * **预设模版**:
     * `general`: 通用剧情概览。
     * `romantic_progression`: 感情线发展。
     * `business_success`: 事业线/复仇线。
     * `suspense_reveal`: 悬疑解密线。
     * `character_growth`: 人物成长弧光。
 * **自定义模式**:
     * `custom`: 使用 `custom_prompts.narrative_focus` 中的内容作为指令。
 
 #### **4. 语言风格 (Style)**
 
 决定 LLM 的语气、用词和口吻。
 
 * **预设模版**:
     * `objective`: 客观纪录片风。
     * `humorous`: 幽默吐槽风。
     * `emotional`: 深情电台风。
     * `suspense`: 悬疑惊悚风。
 * **自定义模式**:
     * `custom`: 使用 `custom_prompts.style` 中的内容作为指令。
 
 #### **5. 角色聚焦 (Character Focus)**
 
 * `mode`: `all` (关注所有主要角色) 或 `specific` (关注特定角色)。
 * `characters`: 字符串数组，例如 `["车小小", "楚昊轩"]`。仅在 `specific` 模式下生效。
 
 ---
 
 ## 4. 输出产物 (Result)
 
 任务成功完成后，`Task.result` 将包含生成的 JSON 数据。
 
 ### 数据结构
 
 ```json
 {
   "generation_date": "2025-11-27T10:00:00",
   "asset_name": "总裁的契约女友",
   "source_corpus": "asset_uuid-org_uuid",
   "narration_script": [
     {
       "narration": "生成的解说词文本...",
       "source_scene_ids": [1, 2, 5], // 对应的来源场景ID列表
       "metadata": {
         "text_len": 120, // 字数
         "pred_audio_duration": 28.5, // 预估朗读时长
         "real_visual_duration": 30.0, // 画面物理时长
         "duration_limit": 25.5, // 策略限制时长 (如 -15% tolerance)
         "overflow_sec": 3.0, // 溢出秒数 (负数表示未溢出)
         "refined": true // true 表示该段落触发了自动缩写
       }
     }
   ],
   "ai_total_usage": {
     "cost_usd": 0.015, // 本次生成预估成本 (美元)
     "cost_rmb": 0.108, // 本次生成预估成本 (人民币)
     "total_tokens": 8000
   }
 }
 ```
 
 ### 错误排查
 
 * **HTTP 400 (Bad Request)**: 通常是 `payload` 格式校验失败。请检查 `scope.type` 是否合法，或 `first_person` 是否缺少了角色名。
 * **Validation Failed**: 日志中出现此错误说明 AI 生成的内容无论如何缩写都无法满足时长要求（极罕见），系统会保留最后一次缩写的结果并在 `metadata` 中标记错误。