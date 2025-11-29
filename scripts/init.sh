#!/bin/bash
# 文件路径: init.sh
# 描述: Visify Cloud - 初始化与部署脚本 (v2.1 修复版)
# 功能: 自动生成分层配置，智能切换多环境，支持 Docker Compose 分层覆盖。

# 遇到错误立即退出
set -e

# --- 自动修正运行路径 ---
# 获取当前脚本所在的绝对路径
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# 假设脚本在 scripts/ 目录下，项目根目录就是上一级
# 如果你打包时把脚本放回了根目录，这里需要判断一下
if [ -f "$SCRIPT_DIR/../docker-compose.base.yml" ]; then
    # 脚本在子目录，切换到父级 (项目根目录)
    cd "$SCRIPT_DIR/.."
    echo "📂 [Context] 切换工作目录至项目根目录: $(pwd)"
elif [ -f "$SCRIPT_DIR/docker-compose.base.yml" ]; then
    # 脚本已经在根目录 (生产环境解压后可能的情况)
    cd "$SCRIPT_DIR"
else
    echo "❌ 错误: 无法定位项目根目录 (未找到 docker-compose.base.yml)"
    exit 1
fi

# --- 0. 环境预设配置 (Configuration Map) ---
PROJECT_NAME="vss-cloud"
DEFAULT_BUCKET_DEV="vss-cloud-dev-bucket"
DEFAULT_BUCKET_TEST="vss-cloud-test-bucket"
DEFAULT_BUCKET_DEMO="vss-cloud-demo-bucket"
DEFAULT_BUCKET_PROD="vss-cloud-prod-bucket"

# 基础 Compose 文件 (所有环境通用)
BASE_COMPOSE_FILE="docker-compose.base.yml"

# --- 1. 参数解析与模式设定 ---
MODE="prod"
OVERRIDE_COMPOSE_FILE="docker-compose.prod.yml"
DEBUG_DEFAULT="False"
TARGET_BUCKET="$DEFAULT_BUCKET_PROD"

if [[ "$1" == "--dev" ]]; then
    MODE="dev"
    OVERRIDE_COMPOSE_FILE="docker-compose.dev.yml"
    DEBUG_DEFAULT="True"
    TARGET_BUCKET="$DEFAULT_BUCKET_DEV"
    echo "🔧 [Mode Switch] 运行模式: 本地开发 (Development)"
elif [[ "$1" == "--test" ]]; then
    MODE="test"
    OVERRIDE_COMPOSE_FILE="docker-compose.test.yml"
    DEBUG_DEFAULT="True"
    TARGET_BUCKET="$DEFAULT_BUCKET_TEST"
    echo "🧪 [Mode Switch] 运行模式: 真机测试 (Test/Staging)"
elif [[ "$1" == "--demo" ]]; then
    MODE="demo"
    OVERRIDE_COMPOSE_FILE="docker-compose.prod.yml"
    DEBUG_DEFAULT="False"
    TARGET_BUCKET="$DEFAULT_BUCKET_DEMO"
    echo "🎪 [Mode Switch] 运行模式: 演示环境 (Demo)"
elif [[ "$1" == "--prod" ]]; then
    MODE="prod"
    OVERRIDE_COMPOSE_FILE="docker-compose.prod.yml"
    DEBUG_DEFAULT="False"
    TARGET_BUCKET="$DEFAULT_BUCKET_PROD"
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (Production)"
else
    if [[ -n "$1" ]]; then
        echo "❌ 错误: 未知参数 '$1'"
        echo "   用法: ./init.sh [--dev | --test | --demo | --prod]"
        exit 1
    fi
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (默认)"
fi

# 构建最终的 Compose 命令参数
# 注意顺序: Base 在前, Override 在后
COMPOSE_FLAGS="-p $PROJECT_NAME -f $BASE_COMPOSE_FILE -f $OVERRIDE_COMPOSE_FILE"

echo "   - Base Config: $BASE_COMPOSE_FILE"
echo "   - Config Files: $BASE_COMPOSE_FILE + $OVERRIDE_COMPOSE_FILE"
echo "   - 默认 DEBUG: $DEBUG_DEFAULT"
echo "   - 预设 Bucket: $TARGET_BUCKET"

# --- 2. 初始化日志 ---
LOG_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
GLOBAL_LOG_FILE="deployment_log_${MODE}_${LOG_TIMESTAMP}.log"
exec > >(tee -a "$GLOBAL_LOG_FILE") 2>&1

echo "================================================"
echo "   Visify Cloud - Initialization & Deployment   "
echo "   Time: $LOG_TIMESTAMP"
echo "================================================"

# --- 变量定义 ---
ENV_TEMPLATE_FILE=".env.template"
ENV_FILE=".env"
GCP_CREDENTIALS_FILE="conf/gcp-credentials.json"
NGINX_CONF_TEMPLATE="conf/nginx.template.conf"

# --- 辅助函数 ---
generate_secret() {
    openssl rand -hex 32
}

check_file_exists() {
    if [ ! -f "$1" ]; then
        echo "❌ 错误: 关键文件 '$1' 未找到。"
        exit 1
    fi
}

# --- 3. 环境预检 ---
echo "🔍 [Step 1] 环境预检..."
check_file_exists "$ENV_TEMPLATE_FILE"
check_file_exists "$BASE_COMPOSE_FILE"
check_file_exists "$OVERRIDE_COMPOSE_FILE"

# Nginx 模板仅在非开发模式下强校验
if [[ "$MODE" != "dev" ]]; then
    check_file_exists "$NGINX_CONF_TEMPLATE"
fi

# 特别检查 GCP 凭证
if [ ! -f "$GCP_CREDENTIALS_FILE" ]; then
    echo "⚠️  警告: 未找到 '$GCP_CREDENTIALS_FILE'。"
    if [[ "$MODE" != "dev" ]]; then
         echo "    [非开发环境] 这通常会导致 RAG 或 GCS 任务失败。"
    fi
    if read -p "    是否继续? [y/N]: " confirm_gcp < /dev/tty; then
        if [[ "$confirm_gcp" != "y" && "$confirm_gcp" != "Y" ]]; then
            exit 1
        fi
    else
        echo "    (无法读取输入，默认继续)"
    fi
fi

# --- 4. 配置生成逻辑 ---
echo "📝 [Step 2] 配置管理..."
GENERATE_NEW_ENV=true

if [ -f "$ENV_FILE" ]; then
    echo "⚠️  检测到现有的 '$ENV_FILE'。"
    if read -p "    是否覆盖并重新生成配置? (警告: 将丢失现有密钥) [y/N]: " confirm_overwrite < /dev/tty; then
        if [[ "$confirm_overwrite" == "y" || "$confirm_overwrite" == "Y" ]]; then
            echo "    🔄 正在备份旧配置并准备重新生成..."
            cp "$ENV_FILE" "${ENV_FILE}.bak_${LOG_TIMESTAMP}"
            rm "$ENV_FILE"
            GENERATE_NEW_ENV=true
        else
            echo "    ⏩ 保留现有配置。"
            GENERATE_NEW_ENV=false
        fi
    else
        echo "    (非交互模式: 跳过覆盖)"
        GENERATE_NEW_ENV=false
    fi
fi

if [ "$GENERATE_NEW_ENV" = true ]; then
    echo "   🚀 正在从模板生成新配置 (.env)..."
    cp "$ENV_TEMPLATE_FILE" "$ENV_FILE"

    # --- 4.1 第一类：核心配置 ---
    NEW_SECRET_KEY=$(generate_secret)
    NEW_DB_PASSWORD=$(generate_secret)
    INPUT_DOMAIN="localhost"

    echo "   ------------------------------------------------"
    echo "   [Category 1] 核心基础配置"
    echo "   ------------------------------------------------"
    read -p "   > 请输入服务器域名或IP (用于 Nginx/Allowed Hosts, 例: localhost,或 35.123.45.67 不携带协议和端口): " user_domain < /dev/tty || true
    if [[ -n "$user_domain" ]]; then INPUT_DOMAIN="$user_domain"; fi

    # --- 4.2 第二类：第三方集成 ---
    echo "   ------------------------------------------------"
    echo "   [Category 2] 第三方集成账户 (Google / Aliyun)"
    echo "   (注: 直接回车将使用默认值或空值)"
    echo "   ------------------------------------------------"

    INPUT_GCP_PROJECT=""
    INPUT_GCP_LOCATION=""
    INPUT_GCS_BUCKET="$TARGET_BUCKET"
    INPUT_GOOGLE_KEY=""
    INPUT_PAI_URL=""
    INPUT_PAI_TOKEN=""

    read -p "   > Google Cloud Project ID: " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_GCP_PROJECT="$user_input"; fi

    read -p "   > Google Cloud Location (e.g., us-central1): " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_GCP_LOCATION="$user_input"; fi

    read -p "   > GCS Default Bucket Name [$TARGET_BUCKET]: " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_GCS_BUCKET="$user_input"; fi

    read -p "   > Google API Key (Gemini): " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_GOOGLE_KEY="$user_input"; fi

    read -p "   > Aliyun PAI-EAS Service URL: " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_PAI_URL="$user_input"; fi

    read -p "   > Aliyun PAI-EAS Token: " user_input < /dev/tty || true
    if [[ -n "$user_input" ]]; then INPUT_PAI_TOKEN="$user_input"; fi

    # --- 4.3 写入配置 ---
    DB_USER="vss_cloud_user"
    DB_NAME="vss_cloud_db"
    NEW_DATABASE_URL="postgres://${DB_USER}:${NEW_DB_PASSWORD}@db:5432/${DB_NAME}"

    sed -i.bak "s|SECRET_KEY=.*|SECRET_KEY='${NEW_SECRET_KEY}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_USER=.*|POSTGRES_USER='${DB_USER}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_DB=.*|POSTGRES_DB='${DB_NAME}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD='${NEW_DB_PASSWORD}'|" "$ENV_FILE"
    sed -i.bak "s|DATABASE_URL=.*|DATABASE_URL='${NEW_DATABASE_URL}'|" "$ENV_FILE"
    sed -i.bak "s|DEBUG=.*|DEBUG=${DEBUG_DEFAULT}|" "$ENV_FILE"
    sed -i.bak "s|SERVER_DOMAIN=.*|SERVER_DOMAIN=${INPUT_DOMAIN}|" "$ENV_FILE"

    # 4.3.1. 处理 ALLOWED_HOSTS (保持不变)
    if [[ "$INPUT_DOMAIN" == "localhost" ]]; then
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    else
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=${INPUT_DOMAIN},localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    fi

    # 4.3.2. [新增] 智能计算 CSRF_TRUSTED_ORIGINS
    # 根据当前运行模式，自动判定端口后缀
    CSRF_PORT_SUFFIX=""
    if [[ "$MODE" == "test" ]]; then
        CSRF_PORT_SUFFIX=":8080"  # 测试环境使用 8080
    elif [[ "$MODE" == "dev" ]]; then
        CSRF_PORT_SUFFIX=":8001"  # 开发环境使用 8001
    fi
    # 生产/演示环境 (prod/demo) 使用标准 80/443 端口，无需后缀

    # 构造信任列表：自动包含 http 和 https 两种协议，以及 localhost 备用
    # 格式示例: http://35.180.x.x:8080,https://35.180.x.x:8080
    CSRF_VAL="http://${INPUT_DOMAIN}${CSRF_PORT_SUFFIX},https://${INPUT_DOMAIN}${CSRF_PORT_SUFFIX},http://localhost${CSRF_PORT_SUFFIX},http://127.0.0.1${CSRF_PORT_SUFFIX}"

    # 写入配置
    sed -i.bak "s|CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=${CSRF_VAL}|" "$ENV_FILE"

    echo "   ✅ CSRF 信任源已配置: $CSRF_VAL"

    if [[ -n "$INPUT_GCP_PROJECT" ]]; then sed -i.bak "s|GOOGLE_CLOUD_PROJECT=.*|GOOGLE_CLOUD_PROJECT=${INPUT_GCP_PROJECT}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GCP_LOCATION" ]]; then sed -i.bak "s|GOOGLE_CLOUD_LOCATION=.*|GOOGLE_CLOUD_LOCATION=${INPUT_GCP_LOCATION}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GCS_BUCKET" ]]; then sed -i.bak "s|GCS_DEFAULT_BUCKET=.*|GCS_DEFAULT_BUCKET=${INPUT_GCS_BUCKET}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GOOGLE_KEY" ]]; then sed -i.bak "s|GOOGLE_API_KEY=.*|GOOGLE_API_KEY=${INPUT_GOOGLE_KEY}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_PAI_URL" ]]; then sed -i.bak "s|PAI_EAS_SERVICE_URL=.*|PAI_EAS_SERVICE_URL=${INPUT_PAI_URL}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_PAI_TOKEN" ]]; then sed -i.bak "s|PAI_EAS_TOKEN=.*|PAI_EAS_TOKEN=${INPUT_PAI_TOKEN}|" "$ENV_FILE"; fi

    rm "${ENV_FILE}.bak"
    echo "✅ .env 配置已生成 (DEBUG=${DEBUG_DEFAULT}, Bucket=${INPUT_GCS_BUCKET})。"
else
    # 不覆盖时的智能检查
    CURRENT_DEBUG=$(grep "^DEBUG=" "$ENV_FILE" | cut -d '=' -f 2)
    if [[ "$MODE" == "dev" && "$CURRENT_DEBUG" == "False" ]]; then
        echo "⚠️  警告: 检测到开发模式 (--dev) 但 DEBUG=False。"
        if read -p "    是否自动修改为 DEBUG=True? [Y/n]: " auto_fix_debug < /dev/tty; then
             if [[ "$auto_fix_debug" != "n" ]]; then
                sed -i.bak "s|DEBUG=False|DEBUG=True|" "$ENV_FILE"
                rm "${ENV_FILE}.bak"
                echo "    ✅ 已修正为 DEBUG=True"
             fi
        fi
    elif [[ "$MODE" != "dev" && "$CURRENT_DEBUG" == "True" ]]; then
        echo "⚠️  严重警告: 检测到生产/演示模式 但 DEBUG=True。"
        if read -p "    是否自动修改为 DEBUG=False? [Y/n]: " auto_fix_prod < /dev/tty; then
             if [[ "$auto_fix_prod" != "n" ]]; then
                sed -i.bak "s|DEBUG=True|DEBUG=False|" "$ENV_FILE"
                rm "${ENV_FILE}.bak"
                echo "    ✅ 已修正为 DEBUG=False"
             fi
        fi
    fi
fi

# --- 4.4 Docker 仓库登录检查 (增强提示版) ---
check_docker_login() {
    # 只有在生产/演示/测试模式下（需要拉取远程镜像）才检查
    if [[ "$MODE" == "dev" ]]; then
        return
    fi

    echo "🔍 [Step 2.5] 检查镜像仓库权限..."

    TARGET_REGISTRY="ghcr.io"
    DOCKER_CONFIG_FILE="$HOME/.docker/config.json"

    if [ ! -f "$DOCKER_CONFIG_FILE" ]; then
        echo "❌ 错误: 未找到 Docker 认证配置文件 ($DOCKER_CONFIG_FILE)。"
        if [ "$(id -u)" == "0" ]; then
            echo "   ⚠️  诊断: 检测到脚本正以 ROOT 权限 (sudo) 运行，正在查找 /root/.docker/config.json。"
            echo "          如果您之前是用普通用户登录的 Docker，Root 账号是读取不到的。"
            echo "   ✅ 解决: 请务必使用 sudo 重新登录一次:"
            echo "          sudo docker login $TARGET_REGISTRY -u <YOUR_GITHUB_USERNAME>"
        else
            echo "   原因: 您尚未在此服务器上登录 Docker 仓库。"
            echo "   解决: 请运行以下命令登录 (使用 GitHub PAT 作为密码):"
            echo "         docker login $TARGET_REGISTRY -u <YOUR_GITHUB_USERNAME>"
        fi
        exit 1
    fi

    if ! grep -q "$TARGET_REGISTRY" "$DOCKER_CONFIG_FILE"; then
        echo "❌ 错误: 在 $DOCKER_CONFIG_FILE 中未检测到 $TARGET_REGISTRY 的登录凭证。"
        if [ "$(id -u)" == "0" ]; then
             echo "   ✅ 解决: 请使用 sudo 重新登录:"
             echo "          sudo docker login $TARGET_REGISTRY -u <YOUR_GITHUB_USERNAME>"
        else
             echo "   ✅ 解决: 请运行命令登录:"
             echo "          docker login $TARGET_REGISTRY -u <YOUR_GITHUB_USERNAME>"
        fi
        exit 1
    else
        echo "✅ 已检测到 $TARGET_REGISTRY 登录凭证。"
    fi
}

check_docker_login

# --- 4.5 生产环境数据目录检查 (兜底策略) ---
# 虽然 package_deploy.sh 已经创建了目录，但保留此段作为安全兜底。
if [[ "$MODE" == "prod" ]]; then
    # echo "📂 [Step 4.5] 验证数据目录结构..."
    # -p 参数保证了如果目录已存在，不会报错；如果不存在（如git pull部署），则自动创建
    mkdir -p "./prod_data/postgres"
    mkdir -p "./prod_data/redis"
fi

# --- 5. 启动基础服务 ---
echo "🚀 [Step 3] 启动基础服务 (Database & Redis)..."
# [核心修正] 使用 COMPOSE_FLAGS (不加引号，以便展开为多个参数)
docker compose $COMPOSE_FLAGS up -d db redis

echo "   等待数据库初始化 (10秒)..."
sleep 10

# --- 6. 数据库迁移 ---
echo "📦 [Step 4] 执行数据库迁移..."
if docker compose $COMPOSE_FLAGS run --rm --no-deps web python manage.py migrate; then
    echo "✅ 数据库迁移成功。"
else
    echo "❌ 数据库迁移失败，请检查日志。"
    exit 1
fi

# --- 7. 静态文件收集 ---
echo "🎨 [Step 5] 收集静态文件 (Collectstatic)..."
if docker compose $COMPOSE_FLAGS run --rm --no-deps web python manage.py collectstatic --noinput; then
    echo "✅ 静态文件收集成功。"
else
    echo "❌ 静态文件收集失败。"
    exit 1
fi

# --- 8. 创建超级用户 (可选) ---
echo "👤 [Step 6] 超级用户设置"
if read -p "   是否现在创建 Django 超级管理员? [y/N]: " create_admin < /dev/tty; then
    if [[ "$create_admin" == "y" || "$create_admin" == "Y" ]]; then
        echo "   >>> 请在下方输入管理员信息 <<<"
        docker compose $COMPOSE_FLAGS run --rm -it web python manage.py createsuperuser
    else
        echo "   (跳过创建管理员)"
    fi
else
    echo "   ⚠️ 无法读取终端输入 (非交互环境)。"
    echo "   请手动运行: docker compose $COMPOSE_FLAGS run --rm -it web python manage.py createsuperuser"
fi

# --- 9. 全量启动 ---
echo "🔥 [Step 7] 启动所有服务..."

# 统一执行启动命令
docker compose $COMPOSE_FLAGS up -d

# 根据不同模式显示差异化的访问提示
if [[ "$MODE" == "dev" ]]; then
    echo "✅ 开发环境 (Dev) 启动完毕！"
    echo "   访问地址: http://${INPUT_DOMAIN:-localhost}:8001"

elif [[ "$MODE" == "test" ]]; then
    echo "✅ 真机测试环境 (Test/Staging) 部署完毕！"
    # [核心修正] 明确提示 8080 端口
    echo "   访问地址: http://${INPUT_DOMAIN:-localhost}:8080/admin"
    echo "   静态资源: Nginx Local Hosting (Gzip Enabled)"

else
    echo "✅ 生产/演示环境 (Prod/Demo) 部署完毕！"
    # 生产环境默认为 80 端口，无需后缀
    echo "   访问地址: http://${INPUT_DOMAIN:-localhost}/admin"
    echo "   静态资源: Nginx Local Hosting (Gzip Enabled)"
fi

echo "================================================"
echo "   状态检查: docker compose $COMPOSE_FLAGS ps"
echo "================================================"

# --- 10. 结束暂停 ---
echo ""
read -n 1 -s -r -p "✅ 脚本执行完毕，请按任意键关闭窗口..." < /dev/tty || true
echo ""