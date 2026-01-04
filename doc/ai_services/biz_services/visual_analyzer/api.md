# Visual Analyzer API

## 1. 概述 (Overview)
`Visual Analyzer` 是一个原子化的视觉分析服务，旨在对批量图片（视频帧）进行独立的语义标注。
它剥离了具体的业务上下文（如剧情、字幕），仅关注画面本身的视觉特征。

**核心能力：**
*   **多维度分析**：提取景别、环境、主体、动作、光影时段、视觉氛围标签。
*   **多语言支持**：支持中文/英文的提示词引导和结果输出。
*   **原子化设计**：输入为纯粹的帧列表，输出为结构化的视觉元数据。

## 2. 接口定义 (Interface)

该服务通过统一的任务管理接口调用。

*   **Endpoint**: `POST /api/v1/tasks/`
*   **Task Type**: `VISUAL_ANALYZER`

### 2.1 请求参数 (Request Payload)

**模式 A：生产模式 (推荐)**

适用于大量数据，先将 `frames` 列表保存为 JSON 文件上传，然后传递文件路径。

```json
{
    "task_type": "VISUAL_ANALYZER",
    "payload": {
        "lang": "zh",
        "visual_model": "models/gemini-1.5-flash-002",
        "frames_file_path": "temp/visual_analyzer_input_task123.json"
    }
}
```

**模式 B：调试模式 (Direct Payload)**

适用于少量数据的快速测试。

```json
{
    "task_type": "VISUAL_ANALYZER",
    "payload": {
        "lang": "zh",
        "visual_model": "models/gemini-1.5-flash-002",
        "frames": [
            {
                "frame_id": "unique_id_001",
                "path": "gs://bucket/path/to/image.jpg",
                "digest": "optional_hash"
            },
            {
                "frame_id": "unique_id_002",
                "path": "relative/path/to/local/image.jpg"
            }
        ]
    }
}
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `lang` | string | 否 | 目标语言代码 (`zh`, `en`)。决定了提示词语言和输出结果的语言。默认为 `en`。 |
| `visual_model` | string | 是 | 指定使用的 VLM 模型版本 (e.g., `models/gemini-2.0-flash-exp`)。 |
| `frames_file_path` | string | 选填 | **(推荐)** 包含帧列表的外部 JSON 文件路径。支持 GCS (`gs://`) 或本地路径。 |
| `frames` | list | 选填 | **(调试用)** 直接在 Payload 中传递的帧列表。 |
| `frames` (文件内容) | list | - | 如果使用文件模式，文件内容应为 `VisualFrameInput` 的 JSON 数组。 |
| `[].frame_id` | string | 是 | 帧的唯一标识符，用于在响应中映射结果及服务端缓存 Key。 |
| `[].path` | string | 是 | 图片路径。支持 GCS URI (`gs://...`) 或服务器本地相对路径。 |
| `[].digest` | string | 否 | 文件摘要（预留字段，暂未校验）。 |

### 2.2 响应结果 (Response Output)

任务完成后，输出结果将保存为 JSON 文件。

```json
{
  "annotated_frames": [
    {
      "frame_id": "unique_id_001",
      "visual_analysis": {
        "shot_type": "特写",
        "environment": "室内-走廊",
        "subject": "人物的腿和脚，穿着黑色高跟鞋",
        "action": "迈步向前行走",
        "lighting_time": "室内明亮",
        "visual_mood_tags": [
          "动态",
          "简洁",
          "职业"
        ]
      }
    }
  ],
  "stats": {
    "total_frames": 1,
    "processed": 1
  },
  "usage_report": {
    "cost_usd": 0.0001,
    "total_tokens": 150
  }
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `annotated_frames` | list | 分析结果列表。 |
| `frame_id` | string | 对应输入的 `frame_id`。 |
| `visual_analysis` | object | 视觉分析详情。 |
| `shot_type` | string | 景别。**注意**：API 内部使用标准枚举 (Enum)，但在输出时会根据请求的 `lang` 自动转换为对应的本地化标签 (Label)。 |
| `environment` | string | 物理环境描述。 |
| `subject` | string | 画面主体描述。 |
| `action` | string | 画面内可见的瞬时物理动作。 |
| `lighting_time` | string | 光影或时间特征。 |
| `visual_mood_tags` | list | 视觉氛围标签列表。 |

## 3. 景别枚举与翻译 (Shot Types)

`shot_type` 字段在底层使用标准枚举，输出时会映射为以下文本：

| Enum Key | 中文 (zh) | 英文 (en) |
| :--- | :--- | :--- |
| `extreme_close_up` | 大特写 | Extreme Close Up |
| `close_up` | 特写 | Close Up |
| `medium_close_up` | 近景 | Medium Close Up |
| `medium_shot` | 中景 | Medium Shot |
| `medium_long_shot` | 中远景 | Medium Long Shot |
| `long_shot` | 远景 | Long Shot |
| `extreme_long_shot` | 大远景 | Extreme Long Shot |
| `establishing_shot` | 建立镜头 | Establishing Shot |
| `other` | 其他 | Other |

## 4. 最佳实践

1.  **独立性**：该接口将每张图片视为独立帧处理。不要依赖图片之间的顺序或上下文关系。
2.  **Batch 处理**：建议一次请求包含 20-50 张图片，以获得最佳的吞吐量和成本效益。
3.  **缓存机制**：服务内部基于 `frame_id` 进行缓存。如果需要重新分析同一张图（例如更换了 Prompt 或 模型），建议更换 `frame_id` 或清理服务端缓存。