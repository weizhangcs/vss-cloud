#!/bin/bash
# 文件路径: init.sh
# 描述: Visify Story Studio (Cloud) - 初始化与部署脚本
# 功能: 自动生成分层配置，智能切换 Dev/Prod 环境，执行数据库迁移与服务启动。
# 用法: ./init.sh [--dev | --prod] (默认为 --prod)

# 遇到错误立即退出
set -e

# --- 0. 参数解析与模式设定 ---
MODE="prod"
COMPOSE_FILE="docker-compose.prod.yml"
DEBUG_DEFAULT="False"

if [[ "$1" == "--dev" ]]; then
    MODE="dev"
    COMPOSE_FILE="docker-compose.dev.yml"
    DEBUG_DEFAULT="True"
    echo "🔧 [Mode Switch] 运行模式: 本地开发 (Development)"
    echo "   - Docker Compose: $COMPOSE_FILE"
    echo "   - 默认 DEBUG: True"
elif [[ "$1" == "--prod" ]]; then
    MODE="prod"
    COMPOSE_FILE="docker-compose.prod.yml"
    DEBUG_DEFAULT="False"
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (Production)"
    echo "   - Docker Compose: $COMPOSE_FILE"
    echo "   - 默认 DEBUG: False"
else
    if [[ -n "$1" ]]; then
        echo "❌ 错误: 未知参数 '$1'"
        echo "   用法: ./init.sh [--dev | --prod]"
        exit 1
    fi
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (默认)"
    echo "   (提示: 使用 ./init.sh --dev 可切换至开发模式)"
fi

# --- 1. 初始化日志 ---
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

# --- 2. 环境预检 ---
echo "🔍 [Step 1] 环境预检..."
check_file_exists "$ENV_TEMPLATE_FILE"
check_file_exists "$COMPOSE_FILE"
check_file_exists "$NGINX_CONF_TEMPLATE"

# 特别检查 GCP 凭证 (属于第二类配置的文件部分)
if [ ! -f "$GCP_CREDENTIALS_FILE" ]; then
    echo "⚠️  警告: 未找到 '$GCP_CREDENTIALS_FILE'。"
    if [[ "$MODE" == "prod" ]]; then
         echo "    [生产环境] 这通常会导致 RAG 或 GCS 任务失败。"
    fi
    if read -p "    是否继续? [y/N]: " confirm_gcp < /dev/tty; then
        if [[ "$confirm_gcp" != "y" && "$confirm_gcp" != "Y" ]]; then
            exit 1
        fi
    else
        echo "    (无法读取输入，默认继续)"
    fi
fi

# --- 3. 配置生成逻辑 ---
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

    # --- 3.1 第一类：核心配置 (自动生成 + 交互) ---
    NEW_SECRET_KEY=$(generate_secret)
    NEW_DB_PASSWORD=$(generate_secret)

    INPUT_DOMAIN="localhost"

    echo "   ------------------------------------------------"
    echo "   [Category 1] 核心基础配置"
    echo "   ------------------------------------------------"
    read -p "   > 请输入服务器域名或IP (用于 Nginx/Allowed Hosts, 例: localhost): " user_domain < /dev/tty || true
    if [[ -n "$user_domain" ]]; then INPUT_DOMAIN="$user_domain"; fi

    # --- 3.2 第二类：第三方集成 (交互式完整覆盖) ---
    echo "   ------------------------------------------------"
    echo "   [Category 2] 第三方集成账户 (Google / Aliyun)"
    echo "   (注: 留空则在该环节跳过，后续需手动编辑 .env 补充)"
    echo "   ------------------------------------------------"

    # Google Cloud
    read -p "   > Google Cloud Project ID: " INPUT_GCP_PROJECT < /dev/tty || true
    read -p "   > Google Cloud Location (e.g., us-central1): " INPUT_GCP_LOCATION < /dev/tty || true
    read -p "   > GCS Default Bucket Name: " INPUT_GCS_BUCKET < /dev/tty || true
    read -p "   > Google API Key (Gemini): " INPUT_GOOGLE_KEY < /dev/tty || true

    # Aliyun PAI-EAS
    read -p "   > Aliyun PAI-EAS Service URL: " INPUT_PAI_URL < /dev/tty || true
    read -p "   > Aliyun PAI-EAS Token: " INPUT_PAI_TOKEN < /dev/tty || true

    # --- 3.3 写入配置 ---

    # [Cat 1]
    DB_USER="visify_cloud_user"
    DB_NAME="visify_story_studio_db"
    NEW_DATABASE_URL="postgres://${DB_USER}:${NEW_DB_PASSWORD}@db:5432/${DB_NAME}"

    sed -i.bak "s|SECRET_KEY=.*|SECRET_KEY='${NEW_SECRET_KEY}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD='${NEW_DB_PASSWORD}'|" "$ENV_FILE"
    sed -i.bak "s|DATABASE_URL=.*|DATABASE_URL='${NEW_DATABASE_URL}'|" "$ENV_FILE"
    sed -i.bak "s|DEBUG=.*|DEBUG=${DEBUG_DEFAULT}|" "$ENV_FILE"
    sed -i.bak "s|SERVER_DOMAIN=.*|SERVER_DOMAIN=${INPUT_DOMAIN}|" "$ENV_FILE"

    # [优化] 智能构建 ALLOWED_HOSTS，避免 localhost 重复
    if [[ "$INPUT_DOMAIN" == "localhost" ]]; then
        # Case 1 & 2: 用户未输入(默认)或显式输入 localhost -> 只保留基础列表
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    else
        # Case 3: 用户输入了其他 IP/域名 -> 将其追加到列表首位
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=${INPUT_DOMAIN},localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    fi

    # [Cat 2] - 写入用户输入的值
    sed -i.bak "s|GOOGLE_CLOUD_PROJECT=.*|GOOGLE_CLOUD_PROJECT=${INPUT_GCP_PROJECT}|" "$ENV_FILE"
    sed -i.bak "s|GOOGLE_CLOUD_LOCATION=.*|GOOGLE_CLOUD_LOCATION=${INPUT_GCP_LOCATION}|" "$ENV_FILE"
    sed -i.bak "s|GCS_DEFAULT_BUCKET=.*|GCS_DEFAULT_BUCKET=${INPUT_GCS_BUCKET}|" "$ENV_FILE"
    sed -i.bak "s|GOOGLE_API_KEY=.*|GOOGLE_API_KEY=${INPUT_GOOGLE_KEY}|" "$ENV_FILE"

    sed -i.bak "s|PAI_EAS_SERVICE_URL=.*|PAI_EAS_SERVICE_URL=${INPUT_PAI_URL}|" "$ENV_FILE"
    sed -i.bak "s|PAI_EAS_TOKEN=.*|PAI_EAS_TOKEN=${INPUT_PAI_TOKEN}|" "$ENV_FILE"

    # [Cat 3] - 第三类配置已在 .env.template 中作为默认值存在，直接保留即可，无需 sed 修改。

    rm "${ENV_FILE}.bak"
    echo "✅ .env 配置已生成 (DEBUG=${DEBUG_DEFAULT})。"
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
    elif [[ "$MODE" == "prod" && "$CURRENT_DEBUG" == "True" ]]; then
        echo "⚠️  严重警告: 检测到生产模式 (--prod) 但 DEBUG=True。"
        if read -p "    是否自动修改为 DEBUG=False? [Y/n]: " auto_fix_prod < /dev/tty; then
             if [[ "$auto_fix_prod" != "n" ]]; then
                sed -i.bak "s|DEBUG=True|DEBUG=False|" "$ENV_FILE"
                rm "${ENV_FILE}.bak"
                echo "    ✅ 已修正为 DEBUG=False"
             fi
        fi
    fi
fi

# --- 4. 启动基础服务 ---
echo "🚀 [Step 3] 启动基础服务 (Database & Redis)..."
docker compose -f "$COMPOSE_FILE" up -d db redis

echo "   等待数据库初始化 (10秒)..."
sleep 10

# --- 5. 数据库迁移 ---
echo "📦 [Step 4] 执行数据库迁移..."
if docker compose -f "$COMPOSE_FILE" run --rm --no-deps web python manage.py migrate; then
    echo "✅ 数据库迁移成功。"
else
    echo "❌ 数据库迁移失败，请检查日志。"
    exit 1
fi

# --- 6. 静态文件收集 ---
echo "🎨 [Step 5] 收集静态文件 (Collectstatic)..."
if docker compose -f "$COMPOSE_FILE" run --rm --no-deps web python manage.py collectstatic --noinput; then
    echo "✅ 静态文件收集成功。"
else
    echo "❌ 静态文件收集失败。"
    exit 1
fi

# --- 7. 创建超级用户 (可选) ---
echo "👤 [Step 6] 超级用户设置"
if read -p "   是否现在创建 Django 超级管理员? [y/N]: " create_admin < /dev/tty; then
    if [[ "$create_admin" == "y" || "$create_admin" == "Y" ]]; then
        echo "   >>> 请在下方输入管理员信息 <<<"
        docker compose -f "$COMPOSE_FILE" run --rm -it web python manage.py createsuperuser
    else
        echo "   (跳过创建管理员)"
    fi
else
    echo "   ⚠️ 无法读取终端输入 (非交互环境)。"
    echo "   请手动运行: docker compose -f $COMPOSE_FILE run --rm -it web python manage.py createsuperuser"
fi

# --- 8. 全量启动 ---
echo "🔥 [Step 7] 启动所有服务..."
if [[ "$MODE" == "dev" ]]; then
    docker compose -f "$COMPOSE_FILE" up -d
    echo "✅ 开发环境启动完毕！"
    echo "   访问地址: http://${INPUT_DOMAIN:-localhost}:8001"
else
    docker compose -f "$COMPOSE_FILE" up -d
    echo "✅ 生产环境部署完毕！"
    echo "   访问地址: http://${INPUT_DOMAIN:-localhost}"
fi

echo "================================================"
echo "   状态检查: docker compose -f $COMPOSE_FILE ps"
echo "================================================"

# --- 9. 结束暂停 (防止窗口闪退) ---
echo ""
# [新增] 暂停等待用户确认，使用 /dev/tty 确保在脚本重定向时也能暂停
read -n 1 -s -r -p "✅ 脚本执行完毕，请按任意键关闭窗口..." < /dev/tty || true
echo ""