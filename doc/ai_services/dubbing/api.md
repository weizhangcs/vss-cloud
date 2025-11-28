 # VSS Cloud API 集成指南 (Unified)
 
 适用版本: v1.2.1 (Narration V3 + Dubbing V2)
 最后更新: 2025-11-28
 
 ---
 
 ## 1. 任务流程概览
 
 VSS Cloud 支持 **"一次创作，多次分发"** 的工业级生产流程：
 
 ### 阶段一：核心创作 (Creation)
 1.  **RAG 部署 (DEPLOY_RAG_CORPUS)**: 初始化知识库。
 2.  **生成母本 (GENERATE_NARRATION)**: 生成带导演指令和元数据的中文母本。
 3.  **母本配音 (GENERATE_DUBBING)**: 基于母本生成中文配音。
 
 ### 阶段二：多语言分发 (Distribution)
 1.  **本地化 (LOCALIZE_NARRATION)**: 基于母本上下文，翻译并生成目标语言的发行脚本（含配音指令）。
 2.  **发行版配音 (GENERATE_DUBBING)**: 基于发行脚本生成目标语言配音。
 
 所有任务创建均使用端点: `POST /api/v1/tasks/`
 
 ---
 
 ## 2. 任务详解：配音与本地化
 
 ### 2.1 生成配音 (Generate Dubbing)
 
 将文本脚本转化为音频文件。系统支持两种截然不同的配音策略，请根据业务需求选择。
 
 **Task Type**: `GENERATE_DUBBING`
 
 #### 策略 A: Google Gemini TTS (推荐)
 * **特点**: 情感丰富，支持长文本（不切分），支持“导演指令” (`[sigh]`, `[laugh]`)。
 * **适用**: 多语言分发、情感类解说、短视频。
 * **数据源**: 优先使用 `narration_for_audio` (带标记) 和 `tts_instruct` (动态指令)。
 
 #### 策略 B: Aliyun CosyVoice (传统)
 * **特点**: 支持声音复刻 (Voice Cloning)。需要文本切分 (<90字)。
 * **适用**: 特定角色音色还原。
 * **数据源**: 强制使用 `narration` (纯净文本)，忽略所有情感标记。
 
 #### 请求示例
 
 ```json
 {
   "task_type": "GENERATE_DUBBING",
   "payload": {
     // [必填] 输入脚本路径 (来自 Generate/Localize Narration 的产出)
     "input_narration_path": "tmp/localized_script_EN.json",
     
     "service_params": {
       // --- 场景 1: 使用 Google Gemini (英文/情感) ---
       "template_name": "chinese_gemini_emotional", // 模板名称
       "language_code": "en-US", // [覆盖] 强制指定语言
       "voice_name": "Puck",     // [覆盖] 指定人设 (Puck/Charon/Kore/Fenrir)
       "speaking_rate": 1.0      // 语速 (1.0 标准)
       
       // --- 场景 2: 使用 Aliyun (中文复刻) ---
       // "template_name": "chinese_paieas_replication",
       // "speed": 1.1
     }
   }
 }
 ```
 
 #### 参数详解 (Service Params)
 
 | 参数名 | 适用策略 | 说明 |
 | :--- | :--- | :--- |
 | **template_name** | 所有 | [必填] `dubbing_templates.yaml` 中定义的模板 Key。 |
 | **voice_name** | Google | 指定 Gemini 的人设名称。常用值：<br>`Puck` (幽默/男), `Charon` (深沉/男), `Kore` (冷静/女), `Fenrir` (激动/男), `Aoede` (明快/女)。 |
 | **language_code** | Google | 标准语言代码，如 `en-US`, `cmn-CN`, `fr-FR`。Gemini 人设支持跨语言演绎。 |
 | **model_name** | Google | 指定模型版本，如 `gemini-2.5-pro-tts`。 |
 | **speed** | Aliyun | 语速倍率 (0.5 ~ 2.0)。 |
 
 ---
 
 ### 2.2 本地化解说词 (Localize Narration)
 
 基于已生成的中文母本，利用 RAG 上下文进行“翻译 + 缩写 + 导演”的一站式处理。
 
 **Task Type**: `LOCALIZE_NARRATION`
 
 #### 请求示例
 
 ```json
 {
   "task_type": "LOCALIZE_NARRATION",
   "payload": {
     // [必填] 母本文件路径 (Generate Narration 的产出)
     "master_script_path": "tmp/narration_script_MASTER.json",
     
     // [必填] 蓝图路径 (用于计算目标语言的画面时长限制)
     "blueprint_path": "tmp/narrative_blueprint.json",
     
     "service_params": {
       "lang": "zh",          // 母本源语言
       "target_lang": "en",   // 目标发行语言
       "model": "gemini-2.5-pro",
       
       // [关键] 目标语言的语速标准 (影响时长校验)
       // 英文建议 2.5 (词/秒), 中文建议 4.2 (字/秒)
       "speaking_rate": 2.5,
       
       // [可选] 容忍度策略 (-0.15 表示强制留白 15%)
       "overflow_tolerance": -0.15
     }
   }
 }
 ```
 
 ---
 
 ## 3. 输出产物
 
 ### 配音任务输出
 
 ```json
 {
   "dubbing_script": [
     {
       "narration": "Original text...",
       "audio_file_path": "tmp/dubbing_task_101_audio/narration_001.mp3", // 相对路径
       "duration_seconds": 12.5,
       "error": null
     }
   ]
 }
 ```
 
 ### 错误排查指南
 
 * **Error: Input file not found**: 检查 Payload 中的 `_path` 字段是否正确，且文件确实存在于 `shared_media/tmp/` 下。
 * **Error: Strategy google_tts not found**: 检查后端是否已部署最新的 `google_tts_strategy.py` 代码。
 * **400 Prompt is only supported for Gemini TTS**: 
     * 原因：尝试给标准模型（如 `cmn-CN-Standard-A`）传了 Prompt。
     * 解决：确保 `voice_name` 使用了 Gemini 人设（如 `Puck`），且 `model_name` 为 `gemini-2.5-pro-tts`。