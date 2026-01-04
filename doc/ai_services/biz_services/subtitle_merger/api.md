# Subtitle Merger API

## 1. 概述 (Overview)
`Subtitle Merger` 是一个原子化的字幕处理服务，利用 LLM 对输入的字幕列表进行语义分析，自动合并被错误分割的片段（如断句错误），输出语义完整、时间轴连续的新字幕列表。

**核心能力：**
*   **语义合并**：识别并合并被切断的句子。
*   **时间轴对齐**：自动调整合并后的开始和结束时间。
*   **标点补全**：根据语义补充缺失的标点符号。

## 2. 接口定义 (Interface)

该服务通过统一的任务管理接口调用。

*   **Endpoint**: `POST /api/v1/tasks/`
*   **Task Type**: `SUBTITLE_MERGER`

### 2.1 请求参数 (Request Payload)

为了适应不同的使用场景，接口支持两种数据传递方式：**生产模式**（推荐）和**调试模式**。

#### 模式 A：生产模式 (推荐)
适用于处理完整电影或长视频的字幕（通常包含数千行）。请先将字幕列表保存为 JSON 文件上传，然后传递文件路径。

```json
{
    "task_type": "SUBTITLE_MERGER",
    "payload": {
        "lang": "zh",
        "model": "models/gemini-1.5-flash-002",
        "subtitle_file_path": "temp/subtitle_input_task123.json"
    }
}
```

**文件内容格式 (`subtitle_file_path` 指向的文件):**
```json
[
  {
    "index": 1,
    "start_time": 16.541,
    "end_time": 19.041,
    "content": "不对，从现在起"
  },
  {
    "index": 2,
    "start_time": 19.166,
    "end_time": 21.541,
    "content": "我就是和风集团继承人"
  }
]
```

#### 模式 B：调试模式 (Direct Payload)
适用于开发调试或处理极少量数据。直接在 Payload 中传递字幕列表。

```json
{
    "task_type": "SUBTITLE_MERGER",
    "payload": {
        "lang": "zh",
        "model": "models/gemini-1.5-flash-002",
        "subtitles": [
            {
                "index": 1,
                "start_time": 16.541,
                "end_time": 19.041,
                "content": "不对，从现在起"
            },
            {
                "index": 2,
                "start_time": 19.166,
                "end_time": 21.541,
                "content": "我就是和风集团继承人"
            }
        ]
    }
}
```

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `lang` | string | 否 | 字幕语言代码 (`zh`, `en`)。默认为 `zh`。 |
| `model` | string | 是 | 指定使用的 LLM 模型版本 (e.g., `models/gemini-1.5-flash-002`)。 |
| `subtitle_file_path` | string | 选填 | **(推荐)** 包含字幕列表的外部 JSON 文件路径。支持 GCS (`gs://`) 或本地相对路径 (`SHARED_ROOT` 下)。 |
| `subtitles` | list | 选填 | **(调试用)** 直接在 Payload 中传递的字幕列表。 |
| `subtitles[].index` | int | 是 | 原始字幕行号。 |
| `subtitles[].start_time` | float | 是 | 开始时间（秒）。 |
| `subtitles[].end_time` | float | 是 | 结束时间（秒）。 |
| `subtitles[].content` | string | 是 | 字幕文本内容。 |

### 2.2 响应结果 (Response Output)

任务完成后，输出结果将保存为 JSON 文件。

```json
{
  "merged_subtitles": [
    {
      "index": 1,
      "start_time": 16.541,
      "end_time": 21.541,
      "content": "不对，从现在起，我就是和风集团继承人",
      "original_indices": [1, 2]
    }
  ],
  "stats": {
    "input_count": 2,
    "output_count": 1
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
| `merged_subtitles` | list | 合并后的字幕列表。 |
| `index` | int | 新的序列号。 |
| `start_time` | float | 合并后的开始时间（取最早）。 |
| `end_time` | float | 合并后的结束时间（取最晚）。 |
| `content` | string | 合并并润色后的文本内容。 |
| `original_indices` | list[int] | 该条目由哪些原始 `index` 合并而来。 |
| `stats` | dict | 统计信息（输入行数、输出行数）。 |
| `usage_report` | dict | Token 用量及成本估算。 |

## 3. 最佳实践

1.  **使用文件模式**：对于完整的视频字幕（通常 > 500 行），强烈建议使用 `subtitle_file_path`。这能避免 HTTP 请求体过大，并利用服务的流式/分批处理能力。
2.  **Batch Size**：服务内部默认按 200 行/批进行处理。这是基于 Gemini Flash 模型上下文窗口优化的数值。