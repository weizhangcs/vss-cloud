#!/bin/bash
# 文件路径: init_setup.sh
# 描述: VSS Cloud 服务器环境配置生成脚本
# 功能: 与用户交互，捕获部署决策快照，并最终生成 .env 配置文件 (仅支持 Test/Demo/Prod 服务器环境)。

# 遇到错误立即退出
set -e

# --- 运行路径修正 (V5 精确路径检查) ---
BASE_COMPOSE_FILE="docker-compose.base.yml"
if [ ! -f "$BASE_COMPOSE_FILE" ]; then
    # 尝试切换到父目录 (适配研发环境，脚本在子目录的情况)
    if [ -f "../$BASE_COMPOSE_FILE" ]; then
        cd ..
        echo "📂 [Context] 切换工作目录至项目根目录: $(pwd)"
    else
        echo "❌ 错误: 未找到 $BASE_COMPOSE_FILE。"
        echo "   请确保在项目根目录运行此脚本。"
        exit 1
    fi
fi
echo "📂 [Context] 验证工作目录: $(pwd) (包含 $BASE_COMPOSE_FILE)"

# --- 基础环境配置 ---
PROJECT_NAME="vss-cloud"
# 各环境默认GCS Bucket配置
DEFAULT_BUCKET_TEST="vss-cloud-test-bucket"
DEFAULT_BUCKET_DEMO="vss-cloud-demo-bucket"
DEFAULT_BUCKET_PROD="vss-cloud-prod-bucket"
# 配置文件名
ENV_TEMPLATE_FILE=".env.template"
ENV_FILE=".env"
GCP_CREDENTIALS_FILE="conf/gcp-credentials.json"
NGINX_CONF_TEMPLATE="conf/nginx.template.conf"

# --- 运行模式解析 (仅支持服务器模式) ---
MODE="prod"
DEBUG_DEFAULT="False"
TARGET_BUCKET="$DEFAULT_BUCKET_PROD"

if [[ "$1" == "--test" ]]; then
    MODE="test"
    DEBUG_DEFAULT="True"
    TARGET_BUCKET="$DEFAULT_BUCKET_TEST"
    echo "🧪 [Mode Switch] 运行模式: 真机测试 (Test/Staging)"
elif [[ "$1" == "--demo" ]]; then
    MODE="demo"
    DEBUG_DEFAULT="False"
    TARGET_BUCKET="$DEFAULT_BUCKET_DEMO"
    echo "🎪 [Mode Switch] 运行模式: 演示环境 (Demo)"
elif [[ "$1" == "--prod" ]]; then
    MODE="prod"
    DEBUG_DEFAULT="False"
    TARGET_BUCKET="$DEFAULT_BUCKET_PROD"
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (Production)"
else
    if [[ -n "$1" ]]; then
        echo "❌ 错误: 无效参数 '$1' 或未指定模式。"
        echo "   用法: ./init_setup.sh [--test | --demo | --prod]"
        exit 1
    fi
    echo "🏭 [Mode Switch] 运行模式: 生产环境 (默认)"
fi

# --- 决策捕获：历史残留清理 (Cleanup Decisions) ---
CLEANUP_DATA_DECISION="False"
CLEANUP_IMAGES_DECISION="False"

echo "🧹 [Cleanup Setup] 捕获部署清理决策..."

if [[ "$MODE" == "test" ]]; then
    # 测试环境：默认清理，用户可选择保留数据（镜像清理通常是 True）
    echo "   模式为真机测试，部署时将执行 down -v 清理容器和卷。"

    # 交互式确认数据目录清理
    if read -r -p "    是否保留现有持久化数据目录? [y/N]: " confirm_clean; then
        if [[ "$confirm_clean" == "y" || "$confirm_clean" == "Y" ]]; then
            CLEANUP_DATA_DECISION="False" # 用户的选择是保留（False）
            echo "    ✅ 已标记：部署时保留现有持久化数据。"
        else
            CLEANUP_DATA_DECISION="True" # 用户的选择是清理（True）
            echo "    🗑️  已标记：部署时清空持久化数据目录。"
        fi
    else
        echo "    (非交互模式，默认清空数据 - True)"
        CLEANUP_DATA_DECISION="True"
    fi

    CLEANUP_IMAGES_DECISION="True" # 测试环境默认清理镜像
    echo "   ✅ 已标记：部署时清理 Docker 镜像和缓存。"

else
    # 生产/演示环境：交互式确认数据清理，镜像默认保留
    echo "⚠️  生产/演示模式。部署时将执行 down -v 清理容器和卷。"

    # 交互式确认数据目录清理
    if read -r -p "    是否确认清空持久化数据目录 (./prod_data/postgres & ./prod_data/redis)? [y/N]: " confirm_clean; then
        if [[ "$confirm_clean" == "y" || "$confirm_clean" == "Y" ]]; then
            CLEANUP_DATA_DECISION="True"
            echo "    ✅ 已标记：部署时清空持久化数据目录。"
        else
            CLEANUP_DATA_DECISION="False"
            echo "    ❌ 已标记：部署时保留现有持久化数据。"
        fi
    else
        echo "    (非交互模式，默认跳过数据清理 - False)"
        CLEANUP_DATA_DECISION="False"
    fi

    CLEANUP_IMAGES_DECISION="False" # 生产环境默认保留镜像
    echo "   ⏩ 已标记：跳过 Docker 镜像清理。"
fi
echo "✅ 清理决策捕获完成！"


# --- 辅助函数 ---
generate_secret() {
    openssl rand -hex 32
}
check_file_exists() {
    if [ ! -f "$1" ]; then
        echo "❌ 错误: 关键文件 '$1' 未找到"
        exit 1
    fi
}
validate_env_template_format() {
    if ! command -v dos2unix &> /dev/null; then
        echo "❌ 错误：dos2unix 未安装，请先执行 install_deps.sh"
        exit 1
    fi
    if grep -q $'\r' "$ENV_TEMPLATE_FILE"; then
        echo "⚠️  检测到 .env.template 使用 CRLF 换行符，自动转换为 LF..."
        dos2unix "$ENV_TEMPLATE_FILE"
        echo "✅ .env.template 已转换为 LF 换行符"
    else
        echo "✅ .env.template 换行符格式正确（LF）"
    fi
}


# --- 环境预检 ---
echo "🔍 [Step 1] 环境预检 (配置生成前)..."
check_file_exists "$ENV_TEMPLATE_FILE"
check_file_exists "$BASE_COMPOSE_FILE"

OVERRIDE_COMPOSE_FILE=""
case "$MODE" in
    test) OVERRIDE_COMPOSE_FILE="docker-compose.test.yml";;
    demo|prod) OVERRIDE_COMPOSE_FILE="docker-compose.prod.yml";;
esac
check_file_exists "$OVERRIDE_COMPOSE_FILE"

validate_env_template_format

# Nginx模板校验 (所有服务器模式都需要 Nginx)
check_file_exists "$NGINX_CONF_TEMPLATE"


# GCP凭证文件检查
if [ ! -f "$GCP_CREDENTIALS_FILE" ]; then
    echo "⚠️  警告: 未找到GCP凭证文件 '$GCP_CREDENTIALS_FILE'"
    echo "    此缺失会导致RAG/GCS相关任务失败" # 不再区分dev
    # 交互式确认
    if read -r -p "    是否继续部署? [y/N]: " confirm_gcp; then
        if [[ "$confirm_gcp" != "y" && "$confirm_gcp" != "Y" ]]; then
            exit 1
        fi
    else
        echo "    (非交互模式，默认继续部署)"
    fi
fi


# --- 环境配置生成 ---
echo "📝 [Step 2] 环境配置管理..."
GENERATE_NEW_ENV=true

# 检测现有.env文件，确认是否覆盖
if [ -f "$ENV_FILE" ]; then
    echo "⚠️  检测到现有环境配置文件 '$ENV_FILE'"
    # 交互式确认
    if read -r -p "    是否覆盖并重新生成? (警告: 现有密钥将丢失) [y/N]: " confirm_overwrite; then
        if [[ "$confirm_overwrite" == "y" || "$confirm_overwrite" == "Y" ]]; then
            echo "    🔄 备份旧配置并重新生成..."
            cp "$ENV_FILE" "${ENV_FILE}.bak_$(date +"%Y%m%d_%H%M%S")"
            rm "$ENV_FILE"
            GENERATE_NEW_ENV=true
        else
            echo "    ⏩ 保留现有配置文件"
            GENERATE_NEW_ENV=false
        fi
    else
        echo "    (非交互模式，跳过配置覆盖)"
        GENERATE_NEW_ENV=false
    fi
fi

# 从模板生成新配置
if [ "$GENERATE_NEW_ENV" = true ]; then
    echo "   🚀 从模板生成新环境配置..."
    cp "$ENV_TEMPLATE_FILE" "$ENV_FILE"

    # 核心配置生成
    NEW_SECRET_KEY=$(generate_secret)
    NEW_DB_PASSWORD=$(generate_secret)
    INPUT_DOMAIN="localhost"

    echo "   ------------------------------------------------"
    echo "   [核心基础配置]"
    echo "   ------------------------------------------------"
    # 交互式输入
    read -r -p "   > 服务器域名/IP (例: localhost、35.123.45.67): " user_domain || true
    if [[ -n "$user_domain" ]]; then INPUT_DOMAIN="$user_domain"; fi

    echo "   ------------------------------------------------"
    echo "   [超级管理员配置 (部署后自动创建)]"
    echo "   ------------------------------------------------"

    INPUT_SUPERUSER_USER="admin"
    INPUT_SUPERUSER_EMAIL="admin@example.com"
    INPUT_SUPERUSER_PASS=""

    # 交互式输入：用户名
    read -r -p "   > 超级管理员用户名 [admin]: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_SUPERUSER_USER="$user_input"; fi

    # 交互式输入：邮箱
    read -r -p "   > 超级管理员邮箱 [admin@example.com]: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_SUPERUSER_EMAIL="$user_input"; fi

    # 交互式输入：密码 (新逻辑：用户输入优先级最高)
    read -r -p "   > 超级管理员密码 (留空则自动生成): " user_input || true
    if [[ -n "$user_input" ]]; then
        INPUT_SUPERUSER_PASS="$user_input"
        echo "   ✅ 采用用户输入的密码。"
    else
        # 自动生成高强度超级用户密码
        INPUT_SUPERUSER_PASS=$(generate_secret)
        echo "   ✅ 密码留空，已自动生成 (32位随机密钥)。"
    fi

    NEW_SUPERUSER_PASSWORD="$INPUT_SUPERUSER_PASS" # 使用最终确定的密码

    # 第三方集成配置
    echo "   ------------------------------------------------"
    echo "   [第三方集成配置 (Google/Aliyun)]"
    echo "   (直接回车使用默认值/空值)"
    echo "   ------------------------------------------------"
    INPUT_GCP_PROJECT=""
    INPUT_GCP_LOCATION=""
    INPUT_GCS_BUCKET="$TARGET_BUCKET"
    INPUT_GOOGLE_KEY=""
    INPUT_PAI_URL=""
    INPUT_PAI_TOKEN=""

    read -r -p "   > Google Cloud Project ID: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_GCP_PROJECT="$user_input"; fi

    read -r -p "   > Google Cloud Location (例: us-central1): " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_GCP_LOCATION="$user_input"; fi

    read -r -p "   > GCS默认Bucket名称 [$TARGET_BUCKET]: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_GCS_BUCKET="$user_input"; fi

    read -r -p "   > Google API Key (Gemini): " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_GOOGLE_KEY="$user_input"; fi

    read -r -p "   > 阿里云PAI-EAS服务URL: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_PAI_URL="$user_input"; fi

    read -r -p "   > 阿里云PAI-EAS Token: " user_input || true
    if [[ -n "$user_input" ]]; then INPUT_PAI_TOKEN="$user_input"; fi

    # --- 写入核心配置到.env ---
    DB_USER="vss_cloud_user"
    DB_NAME="vss_cloud_db"
    NEW_DATABASE_URL="postgres://${DB_USER}:${NEW_DB_PASSWORD}@db:5432/${DB_NAME}"

    # 写入超级用户配置
    sed -i.bak "s|^DJANGO_SUPERUSER_USERNAME=.*|DJANGO_SUPERUSER_USERNAME='${INPUT_SUPERUSER_USER}'|" "$ENV_FILE"
    sed -i.bak "s|^DJANGO_SUPERUSER_EMAIL=.*|DJANGO_SUPERUSER_EMAIL='${INPUT_SUPERUSER_EMAIL}'|" "$ENV_FILE"
    sed -i.bak "s|^DJANGO_SUPERUSER_PASSWORD=.*|DJANGO_SUPERUSER_PASSWORD='${NEW_SUPERUSER_PASSWORD}'|" "$ENV_FILE"

    # 1. 写入部署参数快照
    sed -i.bak "s|^DEPLOY_MODE=.*|DEPLOY_MODE=${MODE}|" "$ENV_FILE"
    sed -i.bak "s|^CLEANUP_DATA=.*|CLEANUP_DATA=${CLEANUP_DATA_DECISION}|" "$ENV_FILE"
    sed -i.bak "s|^CLEANUP_IMAGES=.*|CLEANUP_IMAGES=${CLEANUP_IMAGES_DECISION}|" "$ENV_FILE"

    # 2. 写入安全和数据库配置
    sed -i.bak "s|SECRET_KEY=.*|SECRET_KEY='${NEW_SECRET_KEY}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_USER=.*|POSTGRES_USER='${DB_USER}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_DB=.*|POSTGRES_DB='${DB_NAME}'|" "$ENV_FILE"
    sed -i.bak "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD='${NEW_DB_PASSWORD}'|" "$ENV_FILE"
    sed -i.bak "s|DATABASE_URL=.*|DATABASE_URL='${NEW_DATABASE_URL}'|" "$ENV_FILE"
    sed -i.bak "s|DEBUG=.*|DEBUG=${DEBUG_DEFAULT}|" "$ENV_FILE"
    sed -i.bak "s|SERVER_DOMAIN=.*|SERVER_DOMAIN=${INPUT_DOMAIN}|" "$ENV_FILE"

    # 配置ALLOWED_HOSTS
    # 此处不再区分dev/test/prod，统一配置规则
    if [[ "$INPUT_DOMAIN" == "localhost" ]]; then
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    else
        sed -i.bak "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=${INPUT_DOMAIN},localhost,127.0.0.1,0.0.0.0|" "$ENV_FILE"
    fi

    # 配置CSRF_TRUSTED_ORIGINS（按运行模式自动适配端口）
    CSRF_PORT_SUFFIX=""
    if [[ "$MODE" == "test" ]]; then
        CSRF_PORT_SUFFIX=":8080"  # 测试环境端口
    fi
    # 移除原dev模式端口 :8001
    CSRF_VAL="http://${INPUT_DOMAIN}${CSRF_PORT_SUFFIX},https://${INPUT_DOMAIN}${CSRF_PORT_SUFFIX},http://localhost${CSRF_PORT_SUFFIX},http://127.0.0.1${CSRF_PORT_SUFFIX}"
    sed -i.bak "s|CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=${CSRF_VAL}|" "$ENV_FILE"

    echo "   ✅ CSRF信任源配置完成: $CSRF_VAL"

    # 写入第三方集成配置
    if [[ -n "$INPUT_GCP_PROJECT" ]]; then sed -i.bak "s|GOOGLE_CLOUD_PROJECT=.*|GOOGLE_CLOUD_PROJECT=${INPUT_GCP_PROJECT}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GCP_LOCATION" ]]; then sed -i.bak "s|GOOGLE_CLOUD_LOCATION=.*|GOOGLE_CLOUD_LOCATION=${INPUT_GCP_LOCATION}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GCS_BUCKET" ]]; then sed -i.bak "s|GCS_DEFAULT_BUCKET=.*|GCS_DEFAULT_BUCKET=${INPUT_GCS_BUCKET}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_GOOGLE_KEY" ]]; then sed -i.bak "s|GOOGLE_API_KEY=.*|GOOGLE_API_KEY=${INPUT_GOOGLE_KEY}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_PAI_URL" ]]; then sed -i.bak "s|PAI_EAS_SERVICE_URL=.*|PAI_EAS_SERVICE_URL=${INPUT_PAI_URL}|" "$ENV_FILE"; fi
    if [[ -n "$INPUT_PAI_TOKEN" ]]; then sed -i.bak "s|PAI_EAS_TOKEN=.*|PAI_EAS_TOKEN=${INPUT_PAI_TOKEN}|" "$ENV_FILE"; fi

    rm "${ENV_FILE}.bak"
    echo "✅ 环境配置生成完成 (DEBUG=${DEBUG_DEFAULT}, Bucket=${INPUT_GCS_BUCKET})"
else
    # ⏩ 跳过环境配置生成，保留现有 .env 文件
    echo "⏩ 跳过环境配置生成，保留现有 .env 文件"

    # 检查现有配置的DEBUG状态是否匹配运行模式
    CURRENT_DEBUG=$(grep "^DEBUG=" "$ENV_FILE" | cut -d '=' -f 2)
    # 此处无需检查 dev 模式，只检查是否与预设的服务器模式（test/prod/demo）冲突

    if [[ "$MODE" == "test" && "$CURRENT_DEBUG" == "False" ]]; then
        echo "⚠️  警告: 测试模式下DEBUG为False，可能影响调试"
        if read -r -p "    是否自动修改为DEBUG=True? [Y/n]: " auto_fix_debug; then
             if [[ "$auto_fix_debug" != "n" ]]; then
                sed -i.bak "s|DEBUG=False|DEBUG=True|" "$ENV_FILE"
                rm "${ENV_FILE}.bak"
                echo "    ✅ 已修正DEBUG为True"
             fi
        fi
    elif [[ "$MODE" != "test" && "$CURRENT_DEBUG" == "True" ]]; then
        # 适用于 prod/demo 模式
        echo "⚠️  严重警告: 生产/演示模式下DEBUG为True，存在安全风险"
        if read -r -p "    是否自动修改为DEBUG=False? [Y/n]: " auto_fix_prod; then
             if [[ "$auto_fix_prod" != "n" ]]; then
                sed -i.bak "s|DEBUG=True|DEBUG=False|" "$ENV_FILE"
                rm "${ENV_FILE}.bak"
                echo "    ✅ 已修正DEBUG为False"
             fi
        fi
    fi
fi # <--- 关键的 if 闭合标签

echo "================================================"
echo "   配置生成完成，请运行 ./init_exec.sh 开始部署！"
echo "================================================"