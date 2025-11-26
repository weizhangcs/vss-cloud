
## README.md: Visify Story Studio (Cloud) 
 
本项目是 Visify Story Studio 的云原生后端，基于 Django、Celery 和 Docker 架构，提供 AI 驱动的叙事分析和内容生成服务。 
 
### 1. 架构概览 
 
本项目采用标准的“云-边”协同架构： 
 
* 云端 (本项目)：作为“智能编排中心”。负责运行 AI 任务，生成“指令集”（.json 文件）和“轻量级素材”（.wav 文件）。 
* 边缘端 (Edge Instance)：作为“生产和合成中心”。负责管理本地庞大的原始视频媒体资产，并执行最终的视频合成任务。 
 
核心技术栈：Django 4.2 / PostgreSQL / Redis / Celery / Docker / Gemini。 

--- 
 
### 2. 配置与环境分离 
 
本项目采用 Base + Override 的分层配置架构，以最大限度复用通用配置： 
 
| 场景 | 组合方式 (自动处理) | Web 服务器 | 镜像来源 | 
| :--- | :--- | :--- | :--- | 
| 基础配置 | docker-compose.base.yml | (通用骨架) | (通用定义) | 
| 本地开发 | base.yml + dev.yml | Django runserver | 本地源码 build: . | 
| 真机测试 | base.yml + test.yml | Gunicorn + Nginx (8080) | GHCR image: ...:dev | 
| 生产部署 | base.yml + prod.yml | Gunicorn + Nginx (80) | GHCR image: ...:latest | 

---
### 3. 本地开发环境工作指南 
 
本项目提供了自动化脚本 init.sh 来简化环境的首次配置与引导。 
 
#### 3.1. 首次启动 (Initialization) 
 
适用于刚刚 Clone 项目或重置环境后的第一次运行。 
 
1. 准备必要文件： 
 * 将 Google Cloud Service Account 凭证放入：conf/gcp-credentials.json 
 * 准备Google Cloud/Aliyun PAI-EAS的一些配置参数，ghcr.io的登陆信息
2. 运行引导脚本： 
```bash 
  chmod +x init.sh 
  ./init.sh --dev 
```

 > 脚本功能：自动组合 base+dev 配置，生成 .env (DEBUG=True)，启动基础服务，执行迁移和静态文件收集，并引导创建管理员。 
 
#### 3.2. 日常操作 (Daily Routine) 
 
日常研发推荐直接使用 Docker Compose 命令（需显式指定文件组合）： 
 
* 启动服务： 
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.dev.yml up -d 
```

* 停止服务：
```bash
  docker compose -f docker-compose.dev.yml -f docker-compose.dev.yml down 
```

* 查看日志： 
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.dev.yml logs -f 
``` 
* 数据库变更(模型修改后)：
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.dev.yml run --rm web python manage.py makemigrations 
  docker compose -f docker-compose.base.yml -f docker-compose.dev.yml run --rm web python manage.py migrate
``` 
--- 
 
### 4. 生产/测试环境部署指南 
 
本项目拥有一套完整的 DevOps 工具链，支持从打包到服务器初始化的全流程自动化。 
 
#### 4.1. 构建与打包 (本地开发机) 
 
在部署到公网服务器之前，必须构建镜像并推送到容器仓库（例如 GHCR）。使用打包工具生成标准交付物，严禁直接拷贝源码文件夹到生产环境。 

1. 登录容器仓库：
```bash 
  docker login ghcr.io -u YOUR_GITHUB_USERNAME
```
2. 构建镜像并推送： 
```bash 
  # 测试版 
  docker build -t ghcr.io/YOUR_USER/vss-cloud:dev . 
  docker push ghcr.io/YOUR_USER/vss-cloud:dev 
  # 生产版 
  docker tag ...:dev ...:latest 
  docker push ...:latest 
``` 
3. 生成部署包： 
```bash 
  ./package_deploy.sh 
``` 
 * 产出物：dist/vss-cloud-deploy-{date}-{hash}.tar.gz 
 * 注意：此包不包含 .env 和 gcp-credentials.json 等敏感文件。 
 
#### 4.2. 服务器初始化 (目标服务器) 
 
1. 上传并解压：将 tar.gz 上传至服务器并解压。 
2. 环境依赖安装 (仅首次)： 
```bash 
  sudo ./install_deps.sh 
``` 
 * 自动安装 Docker Engine & Compose。 
 * 自动配置当前用户权限 (需重新登录生效)。 
 
#### 4.3. 补全敏感配置 ("拼图"环节) 
 
部署包为了安全故意缺失了以下文件，启动前必须手动补全： 
 
1. GCP 凭证： 
 * 将本地的 conf/gcp-credentials.json 上传到服务器解压目录下的 conf/ 文件夹中。 
2. Docker 认证： 
 * 在服务器上登录镜像仓库，以便拉取私有镜像： 
```bash 
  sudo docker login ghcr.io -u YOUR_USERNAME 
  # 输入 PAT (Personal Access Token) 
  # 注意，一定要用sudo来登录镜像仓库，因为后续init.sh用root权限执行，会检查/ROOT/.docker/configs.json是否包含了登录的授权凭据
``` 
 
#### 4.4. 一键部署与启动 
 
根据目标环境运行初始化脚本，它会自动处理配置生成、端口适配和 CSRF 信任源设置。 
 
* 测试环境 (Test/Staging): 
```bash 
  sudo ./init.sh --test 
  # 效果：使用 :dev 镜像，监听 8080 端口，自动配置 CSRF 信任 http://IP:8080 
``` 
 
* 生产环境 (Production): 
```bash 
  sudo ./init.sh --prod 
  # 效果：使用 :latest 镜像，监听 80 端口，强制 HTTPS (需配合 LB)，关闭 DEBUG 
```  

#### 4.5. 增量更新/升级流程 (Maintenance)
当代码库或镜像有更新时，请执行以下标准化运维步骤（无需再次运行 init.sh）：

* 拉取新镜像：
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.prod.yml pull 
```  
* 应用数据库变更（如果有）：
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.prod.yml run --rm web python manage.py migrate 
```  
* 更新静态文件（如果有前端资源变动）：
```bash
  docker compose -f docker-compose.base.yml -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput 
```  
* 平滑重启服务：
```bash
  # 仅重启业务容器 
  docker compose -f docker-compose.base.yml -f docker-compose.prod.yml up -d --no-deps web worker 
  # 或者全量重启 
  docker compose -f docker-compose.base.yml -f docker-compose.prod.yml up -d 
```  
--- 
 
### 5. Edge 客户端工作流 (API) 
 
Edge 客户端应通过以下步骤与 Cloud API 进行交互： 
 
1. RAG 部署：POST /api/v1/tasks/ -> DEPLOY_RAG_CORPUS 
2. 生成解说词：POST /api/v1/tasks/ -> GENERATE_NARRATION 
3. 生成配音：POST /api/v1/tasks/ -> GENERATE_DUBBING 
4. 生成剪辑脚本：POST /api/v1/tasks/ -> GENERATE_EDITING_SCRIPT 
5. Edge 端执行最终合成。