## README.md: Visify Story Studio (Cloud)

本项目是 Visify Story Studio 的云原生后端，基于 Django、Celery 和 Docker 架构，提供 AI 驱动的叙事分析和内容生成服务。

### 1. 架构概览

本项目采用标准的“云-边”协同架构：

* **云端 (本项目)**：作为“智能编排中心”。负责运行 AI 任务，生成“指令集”（`.json` 文件）和“轻量级素材”（`.wav` 文件）。
* **边缘端 (Edge Instance)**：作为“生产和合成中心”。负责管理本地庞大的原始视频媒体资产，并执行最终的视频合成任务。

核心技术栈：Django 4.2 / PostgreSQL / Redis / Celery / Docker / Gemini。

---

### 2. 配置与环境分离

本项目严格区分开发环境和生产环境的配置：

| 场景 | 配置脚本 | Web 服务器 | 镜像来源 |
| :--- | :--- | :--- | :--- |
| **本地开发** | `docker-compose.dev.yml` | Django `runserver` | 本地 `build: .` |
| **生产部署** | `docker-compose.prod.yml` | Gunicorn + Nginx | GHCR `image: ghcr.io/...` |

---


### 3. 本地开发环境工作指南 
 
本项目提供了自动化脚本 init.sh 来简化环境的首次配置与引导。日常开发则推荐使用标准的 Docker Compose 命令。 
 
#### 3.1. 首次启动 (Initialization) 
 
适用于刚刚 Clone 项目或重置环境后的第一次运行。 
 
1. 准备必要文件： 
 * 将 Google Cloud Service Account 凭证放入：conf/gcp-credentials.json 
 * (可选) 准备 TTS 参考音频：放入 shared_media/resources/tts_references/ 目录。 
2. 运行引导脚本： 
    ```bash
    chmod +x init.sh 
    ./init.sh --dev
    ```
 
 > 脚本功能：自动生成开发环境配置 (.env with DEBUG=True)、启动基础服务、执行数据库迁移、收集静态文件，并交互式引导您创建管理员账号。 
 
#### 3.2. 日常操作 (Daily Routine) 
 
完成初始化后，日常研发请直接使用 Docker Compose 命令，无需重复运行 init.sh。 
 
* 启动服务： 
    ```bash
    docker compose -f docker-compose.dev.yml up -d 
    ```

* 停止服务： 
    ```bash
    docker compose -f docker-compose.dev.yml down 
    ```
 
* 查看实时日志： 
    ```bash
    docker compose -f docker-compose.dev.yml logs -f
    ``` 
 
* 数据库变更 (模型修改后)： 
    ```bash
    docker compose -f docker-compose.dev.yml run --rm web python manage.py makemigrations 
    docker compose -f docker-compose.dev.yml run --rm web python manage.py migrate
    ``` 
 
* 访问入口： 
 * Web 首页: http://localhost:8001 
 * 后台管理: http://localhost:8001/admin/

---

#### 3.3. 镜像管理 (Build & Push)

在部署到公网服务器之前，必须构建最终的生产镜像并推送到容器仓库（例如 GHCR）。

1.  **登录容器仓库**：
    ```bash
    docker login ghcr.io -u YOUR_GITHUB_USERNAME
    ```
2.  **构建生产镜像并打标签**：
    （`Dockerfile` 中已包含 `collectstatic` 步骤）
    ```bash
    docker build -t ghcr.io/YOUR_GITHUB_USERNAME/vss-cloud:latest .
    ```
3.  **推送镜像到仓库**：
    ```bash
    docker push ghcr.io/YOUR_GITHUB_USERNAME/vss-cloud:latest
    ```

---


### 4. 生产环境部署指南 
 
生产部署使用 docker-compose.prod.yml 文件，配合自动化脚本 init.sh 进行标准化交付。 
 
#### 4.1. 部署前准备 
 
1. 凭证文件： 
 * 必须将 Google Cloud Service Account 密钥放置于：conf/gcp-credentials.json 
 * (可选) 确保 TTS 参考音频存在于共享目录。 
2. Nginx 模板： 
 * 确保 conf/nginx.template.conf 文件存在。 
3. 拉取镜像： 
    ```bash
    docker compose -f docker-compose.prod.yml pull
    ```
 
 
#### 4.2. 首次启动流程 (First-time Deployment) 
 
使用 init.sh 脚本的一键部署模式（Production Mode），它会自动处理密钥生成、环境检查、Nginx 域名配置及服务启动。 
 
1. 执行部署脚本： 
    ```bash
    chmod +x init.sh 
    ./init.sh --prod 
    ```
 
2. 脚本交互说明： 
 * 配置生成：脚本会引导您输入服务器域名（用于 Nginx）、GCP Project ID 等关键信息，并自动生成 DEBUG=False 的生产级 .env 文件。 
 * 管理员设置：交互式创建 Django Superuser。 
 * 自动化流程：自动执行 migrate（数据库迁移）和 collectstatic（静态文件收集）。 
 
#### 4.3. 增量更新/升级流程 (Maintenance) 
 
当代码库或镜像有更新时，请执行以下标准化运维步骤（无需再次运行 init.sh）： 
 
1. 拉取新镜像： 
    ```bash
    docker compose -f docker-compose.prod.yml pull 
    ``` 
2. 应用数据库变更（如果有）： 
    ```bash
    docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate 
    ``` 

3. 更新静态文件（如果有前端资源变动）： 
    ```bash
    docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput 
    ``` 

4. 平滑重启服务： 
    ```bash
    # 仅重启业务容器 
    docker compose -f docker-compose.prod.yml up -d --no-deps web worker 
    # 或者全量重启 
    docker compose -f docker-compose.prod.yml up -d 
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