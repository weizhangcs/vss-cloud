## README.md: Visify Story Studio (Cloud)

本项目是 Visify Story Studio 的云原生后端，基于 Django、Celery 和 Docker 架构，提供 AI 驱动的叙事分析和内容生成服务。

### 1. 架构概览

本项目采用标准的“云-边”协同架构：

* [cite_start]**云端 (本项目)**：作为“智能编排中心”。负责运行 AI 任务，生成“指令集”（`.json` 文件）和“轻量级素材”（`.wav` 文件）[cite: 5]。
* [cite_start]**边缘端 (Edge Instance)**：作为“生产和合成中心”。负责管理本地庞大的原始视频媒体资产，并执行最终的视频合成任务[cite: 5]。

核心技术栈：Django 4.2 / PostgreSQL / Redis / Celery / Docker / Gemini。

---

### 2. 配置与环境分离

本项目严格区分开发环境和生产环境的配置：

| 场景 | 配置脚本 | Web 服务器 | 镜像来源 |
| :--- | :--- | :--- | :--- |
| **本地开发** | `docker-compose.dev.yml` | Django `runserver` | [cite_start]本地 `build: .` [cite: 5] |
| **生产部署** | `docker-compose.prod.yml` | Gunicorn + Nginx | GHCR `image: ghcr.io/...` |

---

### 3. 本地开发环境设置

[cite_start]本地开发环境使用 `docker-compose.dev.yml` [cite: 5] 进行快速启动和调试。

#### 3.1. 准备工作

1.  **准备配置文件**：在项目根目录下创建 `.env` 文件和 `conf/gcp-credentials.json` 文件。
2.  **创建共享目录**：创建 `shared_media/resources/tts_references/` 目录，并放入所需的参考音频文件（例如 `zero_shot_prompt.wav`）。
3.  [cite_start]**开发配置文件**：本项目使用 `docker-compose.dev.yml`（对应原 `docker-compose.yml` [cite: 5]）。

#### 3.2. 首次启动 (Build & Migrate)

1.  **构建镜像并启动服务**：
    ```bash
    docker-compose -f docker-compose.dev.yml up -d --build
    ```
2.  **运行数据库迁移**：
    ```bash
    docker-compose -f docker-compose.dev.yml run --rm web python manage.py migrate
    ```
3.  **创建超级用户**：
    ```bash
    docker-compose -f docker-compose.dev.yml run --rm web python manage.py createsuperuser
    ```
    *提示：本地访问地址为 `http://localhost:8000/admin/`*

---

### 4. 生产环境部署指南

生产部署使用 `docker-compose.prod.yml` 文件。

#### 4.1. 部署前准备

1.  **配置文件**：在服务器项目根目录下手动创建并配置 **`.env`** 和 **`conf/gcp-credentials.json`** 文件（包含生产密钥）。
    * **`.env` 必须包含 `SERVER_DOMAIN`**，用于 Nginx 动态配置。
2.  **Nginx 模板**：确保 `nginx.template.conf` 存在。
3.  **拉取镜像**：
    ```bash
    docker-compose -f docker-compose.prod.yml pull
    ```

#### 4.2. 首次启动流程

[cite_start]首次启动时，运行以下命令（对应 `deploy.sh` [cite: 4] 的逻辑）：

1.  [cite_start]**启动数据库和 Redis** [cite: 4]：
    ```bash
    docker-compose -f docker-compose.prod.yml up -d db redis
    ```
2.  [cite_start]**等待数据库初始化** [cite: 4]：
    ```bash
    sleep 15
    ```
3.  [cite_start]**运行数据库迁移** [cite: 4]：
    ```bash
    docker-compose -f docker-compose.prod.yml run --rm --no-deps web python manage.py migrate
    ```
4.  [cite_start]**创建超级用户** [cite: 4]：
    ```bash
    docker-compose -f docker-compose.prod.yml run --rm --no-deps web python manage.py createsuperuser
    ```
5.  **启动所有服务** (web, worker, nginx)：
    ```bash
    docker-compose -f docker-compose.prod.yml up -d
    ```

#### 4.3. 增量更新/升级流程 (日常运维)

此流程用于部署新代码版本。

1.  **拉取新镜像**：
    ```bash
    docker-compose -f docker-compose.prod.yml pull
    ```
2.  **运行迁移 (必须)**：如果新版本有数据库模型更改，此步骤至关重要。
    ```bash
    docker-compose -f docker-compose.prod.yml run --rm --no-deps web python manage.py migrate
    ```
3.  **重启服务**：`web` 和 `worker` 将使用新镜像启动。
    ```bash
    docker-compose -f docker-compose.prod.yml up -d
    ```

---

### 5. Edge 客户端工作流（API）

Edge 客户端应通过以下步骤与 Cloud API 进行交互：

1.  **上传输入**：`POST /api/v1/files/upload/`
2.  **创建配音任务**：`POST /api/v1/tasks/` -> `GENERATE_DUBBING`
3.  **下载配音脚本**：`GET /api/v1/files/tasks/<task_id>/download/`
4.  **下载配音资产 (WAV)**：`GET /api/v1/files/download/?path=...` (通用下载接口)
5.  **创建剪辑任务**：`POST /api/v1/tasks/` -> `GENERATE_EDITING_SCRIPT`
6.  **下载剪辑脚本**：`GET /api/v1/files/tasks/<task_id>/download/`
7.  **Edge 端执行最终合成**。