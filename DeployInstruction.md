### 部署说明文档（DeployInstruction.md）
#### 文档说明
本文档为 VSS Cloud 项目的部署操作指南，涵盖部署包构建、环境依赖安装、服务启动全流程，**当前适配系统：Ubuntu 24.04 LTS、Debian 12 LTS**。不向前兼容，区分生产 / 测试环境配置。

#### 目录结构约定（项目根目录）
 
```
├── DeployInstruction.md # 本部署说明文档 
├── scripts/ 
│ ├── install_deps.sh # 环境依赖安装脚本（Docker+Compose+dos2unix） 
│ ├── init_setup.sh # 项目配置生成脚本（仅交互）
│ ├── init_exec.sh # 项目自动化执行脚本（无交互）
│ └── package_deploy.sh # 部署包构建脚本 
├── docker-compose.base.yml # 基础服务配置（通用） 
├── docker-compose.prod.yml # 生产环境配置 
├── docker-compose.test.yml # 测试环境配置 
├── .env.template # 环境变量模板 
├── conf/ # 配置文件目录（Nginx/GCP凭证等） 
└── dist/ # 部署包输出目录（构建后生成） 
```

### 一、部署包构建（package_deploy.sh）
#### 1. 脚本功能
将项目部署所需核心文件（配置、脚本、目录结构）打包为 tar.gz 压缩包，支持指定版本和环境（生产 / 测试），自动创建对应环境的数据目录。

#### 2. 执行参数与业务场景
| 执行命令 | 业务场景 | 说明 |
|:---|:---|:---|
| `./scripts/package_deploy.sh` | 生产环境 + 默认版本号 | 版本号格式：YYYYMMDD-短 Git 哈希（无 Git 则为 YYYYMMDD-nogit），打包生产环境配置 |
| `./scripts/package_deploy.sh v1.0.0` | 生产环境 + 自定义版本号 | 指定版本号为 `v1.0.0`，打包生产环境配置 |
| `./scripts/package_deploy.sh --env test` | 测试环境 + 默认版本号 | **必须使用 `--env test`**，打包测试环境配置，自动创建 `test_data` 目录 |
| `./scripts/package_deploy.sh v1.0.0 --env test` | 测试环境 + 自定义版本号 | **必须使用 `--env test`**，指定版本号为 `v1.0.0`，打包测试环境配置 |
| `./scripts/package_deploy.sh --help` | 查看帮助 | 输出脚本使用说明，包含参数解释和示例 |

#### 3. 输出结果
构建完成后在 `dist/` 目录生成部署包，命名规则：
- 生产环境：`vss-cloud-deploy-[版本号]-prod.tar.gz`
- 测试环境：`vss-cloud-deploy-[版本号]-test.tar.gz`

### 二、环境依赖安装（install_deps.sh）
#### 1. 脚本功能
自动适配指定操作系统，安装 Docker Engine + Docker Compose + dos2unix 工具，配置 Docker 用户组权限（免 sudo 操作），支持国内 / 海外源切换。

#### 2. 执行参数与业务场景
| 执行命令 | 业务场景 | 说明 |
|:---|:---|:---|
| `sudo ./scripts/install_deps.sh` | 海外源 + 自动检测系统 | 自动识别系统，使用 Docker 官方源安装依赖（含 dos2unix） |
| `sudo ./scripts/install_deps.sh --cn` | 国内源 + 自动检测系统 | 适配国内网络，使用阿里云 Docker 源安装依赖（含 dos2unix） |
| `sudo ./scripts/install_deps.sh --os debian` | 海外源 + 指定 Debian 系统 | 强制按 Debian 逻辑安装，包含 dos2unix 安装 |
| `sudo ./scripts/install_deps.sh --cn --os ubuntu` | 国内源 + 指定 Ubuntu 系统 | 强制按 Ubuntu 逻辑安装，使用阿里云源，包含 dos2unix 安装 |
| `sudo ./scripts/install_deps.sh --help` | 查看帮助 | 输出脚本使用说明，包含参数解释和示例 |

#### 3. 注意事项
执行后普通用户需重新登录或执行 `newgrp docker` 使 Docker 组权限生效；
`dos2unix` 工具会自动完成安装，无需手动执行，安装后用于保障 `.env.template` 文件换行符规范。

### 三、Docker 服务启动（docker-compose 系列配置）
#### 1. 配置文件分工
| 配置文件 | 功能定位 | 核心特性 |
|:---|:---|:---|
| `docker-compose.base.yml` | 基础通用配置 | 定义 DB/Redis/ 应用服务骨架，包含健康检查、时区配置、重启策略等通用规则 |
| `docker-compose.prod.yml` | 生产环境特有配置 | 镜像版本 `latest`、流控并发配置、80 端口暴露、数据目录 `prod_data/` |
| `docker-compose.test.yml` | 测试环境特有配置 | 镜像版本 `dev`、调试端口暴露（5432/6379/8080）、数据目录 `test_data/` |

#### 2. 启动命令与业务场景
**注意：在自动化部署中，这些命令将由 `./init_exec.sh` 自动执行，无需手动调用。**

| 执行命令 | 业务场景 | 说明 |
|:---|:---|:---|
| `docker compose -p vss-cloud -f docker-compose.base.yml -f docker-compose.prod.yml up -d` | 生产环境启动（手动） | 合并基础 + 生产配置，指定项目名称为 `vss-cloud`，后台启动服务，数据持久化到 `prod_data/` 目录 |
| `docker compose -p vss-cloud -f docker-compose.base.yml -f docker-compose.test.yml up -d` | 测试环境启动（手动） | 合并基础 + 测试配置，指定项目名称为 `vss-cloud`，后台启动服务，暴露调试端口，数据持久化到 `test_data/` 目录 |
| `docker compose -p vss-cloud down` | 停止并移除服务 | 指定项目名称为 `vss-cloud`，保留数据目录（`prod_data`/`test_data`），仅停止容器 |
| `docker compose -p vss-cloud down -v` | 停止服务并删除卷 | 指定项目名称为 `vss-cloud`，**谨慎使用！**会删除数据目录中的持久化数据（生产环境禁止） |

#### 3. 关键验证
启动后执行以下命令验证服务状态：
```bash 
# 查看容器运行状态 
docker compose -p vss-cloud ps 
```
```bash
# 查看 DB/Redis 健康检查状态 
docker inspect --format '{{.State.Health.Status}}' [容器名 /db/redis] 
```
```bash
# 验证数据目录挂载（以生产环境为例） 
docker volume inspect vss-cloud_postgres_data 
```

### 四、完整部署流程（生产环境示例）
此流程使用 `init_setup.sh` 和 `init_exec.sh` 脚本，实现了**配置与执行的解耦**。

#### 1. 构建部署包
```bash 
cd 项目根目录 
./scripts/package_deploy.sh v1.0.0 --env prod 
```

#### 2. 上传部署包到服务器
```bash 
# 本地执行（示例） 
scp dist/vss-cloud-deploy-v1.0.0-prod.tar.gz wzhang@服务器IP:/opt/ 
```

#### 3. 服务器端操作（核心自动化流程）
```bash 
# 解压部署包 
cd /opt 
tar -zxvf vss-cloud-deploy-v1.0.0-prod.tar.gz 
cd vss-cloud-deploy-v1.0.0-prod 
```

```bash
# 步骤 A: 安装环境依赖（仅首次）
sudo ./install_deps.sh --cn 

# 步骤 B: 权限生效（必须！）
# 退出并重新登录服务器，或执行：
newgrp docker

# 步骤 C: 镜像仓库登陆鉴权（需手动执行一次）
# 登陆鉴权的目标仓库域名应与您的 docker-compose 配置中使用的镜像域名一致。
# 示例（阿里云）：
docker login --username=hi31856213@aliyun.com crpi-34v4qt829vtet2cy.cn-hangzhou.personal.cr.aliyuncs.com 
# 示例（ghcr.io）：
# docker login ghcr.io -u <您的GitHub用户名>
 
# 步骤 D: 初始化配置 (交互式)
# 脚本将引导您输入域名、GCP配置、管理员信息等，并写入 .env 文件作为部署快照
./init_setup.sh --prod 
 
# 步骤 E: 启动部署 (自动化执行)
# 脚本将读取 .env 快照，自动执行 down/清理数据/拉取镜像/迁移数据库/收集静态文件/创建管理员/启动所有服务
sudo ./init_exec.sh
# 注意：init_exec.sh 需要 sudo 权限来执行完整的 Docker 流程。
```

### 五、常见问题

1. Docker 权限不足：
重新登录服务器，或执行 `newgrp docker` 刷新用户组权限。
2. 数据目录挂载失败：
检查部署包解压后是否存在 `prod_data`/`test_data` 目录，确保 Compose 配置中路径为相对路径（`./prod_data/xxx`）。
3. 测试环境端口冲突：
修改 `docker-compose.test.yml` 中暴露端口（如 5432→5433、6379→6380）。
4. 镜像拉取失败：
检查镜像仓库登陆鉴权是否执行成功，确保使用的 Docker 镜像仓库地址和 `login` 操作匹配。

### 六、版本适配说明
| 系统 | 适配版本 | 特殊说明 |
|:---|:---|:---|
| Ubuntu | 24.04.3 LTS | 无需额外依赖，直接执行脚本，自动安装 dos2unix |
| Debian | 13.2.0 | 无需额外依赖，直接执行脚本，自动安装 dos2unix |