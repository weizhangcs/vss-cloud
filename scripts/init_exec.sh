#!/bin/bash
# 文件路径: init_exec.sh
# 描述: VSS Cloud 自动化部署和初始化执行脚本
# 功能: 读取 .env 配置快照，执行 Docker Compose 部署、等待数据库、迁移等步骤。

# 遇到错误立即退出
set -e

# --- 运行路径修正 ---
BASE_COMPOSE_FILE="docker-compose.base.yml"
if [ ! -f "$BASE_COMPOSE_FILE" ]; then
    # 尝试切换到父目录 (适配研发环境，脚本在子目录的情况)
    if [ -f "../$BASE_COMPOSE_FILE" ]; then
        cd ..
    else
        # 此错误应在 setup 阶段发现，此处为二次检查
        echo "❌ 错误: 未找到 $BASE_COMPOSE_FILE，无法定位项目根目录。"
        exit 1
    fi
fi
echo "📂 [Context] 当前工作目录: $(pwd)"

# --- 1. 变量和模式解析 (从 .env 快照中获取) ---
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ 错误: 未找到环境配置文件 '$ENV_FILE'"
    echo "   请先运行 ./init_setup.sh 生成配置。"
    exit 1
fi

# 从 .env 读取部署参数快照
MODE=$(grep "^DEPLOY_MODE=" "$ENV_FILE" | cut -d '=' -f 2)
CLEANUP_DATA=$(grep "^CLEANUP_DATA=" "$ENV_FILE" | cut -d '=' -f 2)
CLEANUP_IMAGES=$(grep "^CLEANUP_IMAGES=" "$ENV_FILE" | cut -d '=' -f 2)

if [ -z "$MODE" ]; then
    echo "❌ 错误: .env 文件中未找到 DEPLOY_MODE 变量。"
    exit 1
fi

# 根据模式确定 Compose 文件和基础变量
OVERRIDE_COMPOSE_FILE=""
case "$MODE" in
    test) OVERRIDE_COMPOSE_FILE="docker-compose.test.yml";;
    demo|prod) OVERRIDE_COMPOSE_FILE="docker-compose.prod.yml";;
    *) echo "❌ 错误: DEPLOY_MODE '$MODE' 无效。仅支持 test/demo/prod。"; exit 1;;
esac

PROJECT_NAME="vss-cloud"
COMPOSE_FLAGS="-p $PROJECT_NAME -f $BASE_COMPOSE_FILE -f $OVERRIDE_COMPOSE_FILE"
DB_USER="vss_cloud_user"
DB_NAME="vss_cloud_db"
# 读取 SERVER_DOMAIN 用于最终访问信息输出
INPUT_DOMAIN=$(grep "^SERVER_DOMAIN=" "$ENV_FILE" | cut -d '=' -f 2 | tr -d "'") # 移除可能的单引号

# 读取超级用户配置
SUPERUSER_USER=$(grep "^DJANGO_SUPERUSER_USERNAME=" "$ENV_FILE" | cut -d '=' -f 2 | tr -d "'")
SUPERUSER_EMAIL=$(grep "^DJANGO_SUPERUSER_EMAIL=" "$ENV_FILE" | cut -d '=' -f 2 | tr -d "'")
SUPERUSER_PASS=$(grep "^DJANGO_SUPERUSER_PASSWORD=" "$ENV_FILE" | cut -d '=' -f 2 | tr -d "'")

echo "================================================"
echo "   Visify Cloud 自动化部署流程 (快照模式: $MODE) "
echo "   基础配置文件: $BASE_COMPOSE_FILE"
echo "   覆盖配置文件: $OVERRIDE_COMPOSE_FILE"
echo "================================================"

# --- 部署日志初始化 ---
LOG_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
GLOBAL_LOG_FILE="deployment_log_${MODE}_${LOG_TIMESTAMP}.log"
echo "日志写入文件: $GLOBAL_LOG_FILE"

# 关键：进程替换只在 Bash 中支持
exec 3>&1 4>&2 # 备份标准输出和标准错误
exec 1> >(tee -a "$GLOBAL_LOG_FILE") 2>&1 # 将 stdout 和 stderr 重定向到 tee


# --- 2. Docker镜像仓库权限检查 (V5.5 修复) ---
check_docker_login() {
    echo "🔍 [Step 1] 检查镜像仓库权限..."

    # 1. 确定原始调用用户和其家目录
    local caller_user="${SUDO_USER:-$USER}"
    # 使用 getent passwd 查找指定用户的家目录，这是最可靠的方式
    local caller_home_dir=$(getent passwd "$caller_user" | cut -d: -f6)

    # 2. 构造正确的 Docker 配置路径 (针对原始用户)
    local DOCKER_CONFIG_FILE="${caller_home_dir}/.docker/config.json"

    # 3. 确定目标镜像仓库 (根据您的测试日志，修正为阿里云 CR)
    local TARGET_REGISTRY="crpi-34v4qt829vtet2cy.cn-hangzhou.personal.cr.aliyuncs.com"

    if [ "$EUID" -eq 0 ] && [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        echo "   [Diagnostic] 正在以 root (sudo) 权限检查用户 '$caller_user' 的凭证..."
    fi

    if [ ! -f "$DOCKER_CONFIG_FILE" ]; then
        echo "❌ 错误: 未找到Docker认证配置文件 $DOCKER_CONFIG_FILE"
        echo "   (请确保用户 '$caller_user' 已执行 docker login)"
        echo "   解决方案: 请运行 'docker login $TARGET_REGISTRY -u <您的用户名>'"
        exit 1
    fi

    # 检查是否包含目标仓库的登录凭证
    if ! grep -q "$TARGET_REGISTRY" "$DOCKER_CONFIG_FILE"; then
        echo "❌ 错误: 未检测到目标仓库 ($TARGET_REGISTRY) 的登录凭证"
        echo "   解决方案: 请运行 'docker login $TARGET_REGISTRY -u <您的用户名>'"
        exit 1
    else
        echo "✅ 已检测到目标仓库 ($TARGET_REGISTRY) 的登录凭证"
    fi
}
check_docker_login

# --- 3. 历史残留清理与环境初始化执行 (根据快照执行) ---
echo "🧹 [Step 2] 执行历史残留清理..."

# 1. 容器/网络/数据卷清理 (总是执行 down，确保干净启动)
echo "   🔄 移除旧容器、网络和数据卷..."
docker compose $COMPOSE_FLAGS down -v --remove-orphans || true


# 2. 持久化数据清理 (根据配置快照执行)
if [[ "$CLEANUP_DATA" == "True" ]]; then
    echo "   🗑️  根据配置快照，清理持久化数据目录..."
    # 生产目录兜底创建
    mkdir -p "./prod_data/postgres"
    mkdir -p "./prod_data/redis"
    rm -rf ./prod_data/postgres/* || true
    rm -rf ./prod_data/redis/* || true
    echo "   ✅ 数据目录清理完成。"
else
    echo "   ⏩ 根据配置快照，保留现有持久化数据。"
    # 确保目录存在 (非清理模式下也需要)
    mkdir -p "./prod_data/postgres"
    mkdir -p "./prod_data/redis"
fi


# 3. Docker 镜像清理 (根据配置快照执行)
if [[ "$CLEANUP_IMAGES" == "True" ]]; then
    echo "   🗑️  清理未使用的 Docker 镜像和缓存..."
    # 注意：需要以原始用户的身份执行 prune，否则可能会清理掉 root 拉取的镜像
    # 然而，由于我们在脚本开头使用 sudo 切换了权限，这里仍然是 root 权限执行。
    # 既然用户已经拥有 docker 组权限，我们要求用户在部署前清理更合适。
    # 但为了自动化流程，我们保留 root 执行，这通常是可接受的，因为容器是由 root 启动。
    docker system prune -f --volumes || true
    echo "   ✅ 镜像/缓存清理完成！"
else
    echo "   ⏩ 跳过 Docker 镜像清理。"
fi
echo "✅ 历史残留清理执行完成！"


# --- 4. 基础服务启动 ---
echo "🚀 [Step 3] 启动基础服务 (数据库/Redis)..."
docker compose $COMPOSE_FLAGS up -d db redis || { echo "❌ 基础服务启动失败"; exit 1; }

# --- 5. 等待数据库服务就绪 (健壮性优化) ---
echo "   等待数据库服务就绪..."
DB_CONTAINER_ID=$(docker compose $COMPOSE_FLAGS ps -q db)

MAX_RETRIES=30 # 30 * 2秒 = 60秒超时
COUNT=0

while ! docker exec "$DB_CONTAINER_ID" pg_isready -U "$DB_USER" -d "$DB_NAME" > /dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ $COUNT -gt $MAX_RETRIES ]; then
        echo "❌ 数据库服务超时未就绪 (60秒)，终止部署。"
        exit 1
    fi

    # 尝试重新启动 db 服务，以防中间状态失败 (幂等操作)
    if [ $(($COUNT % 5)) -eq 0 ]; then
        echo "   ⚠️  数据库长时间未就绪，尝试重启 db 服务..."
        docker compose $COMPOSE_FLAGS up -d db || true
        DB_CONTAINER_ID=$(docker compose $COMPOSE_FLAGS ps -q db) # 重新获取 ID
    fi

    echo "   ⏳ 数据库尚未就绪，等待2秒 (${COUNT}/${MAX_RETRIES})..."
    sleep 2
done
echo "✅ 数据库服务已就绪！"


# --- 6. 数据库迁移 ---
echo "📦 [Step 4] 执行数据库迁移..."
if docker compose $COMPOSE_FLAGS run --rm --no-deps web python manage.py migrate; then
    echo "✅ 数据库迁移成功"
else
    echo "❌ 数据库迁移失败，请检查部署日志"
    exit 1
fi

# --- 7. 静态文件收集 ---
echo "🎨 [Step 5] 收集静态文件..."
if docker compose $COMPOSE_FLAGS run --rm --no-deps web python manage.py collectstatic --noinput; then
    echo "✅ 静态文件收集成功"
else
    echo "❌ 静态文件收集失败"
    exit 1
fi

# --- 8. 全量服务启动 ---
echo "🔥 [Step 6] 自动化超级用户创建..."
docker compose $COMPOSE_FLAGS up -d

# --- 9. 超级用户创建 (提示) ---
# 确保所有超级用户变量都已填充
if [[ -z "$SUPERUSER_USER" || -z "$SUPERUSER_EMAIL" || -z "$SUPERUSER_PASS" ]]; then
    echo "❌ 错误: 超级用户配置不完整，跳过自动创建。"
    echo "   请检查 .env 文件中的 DJANGO_SUPERUSER_* 变量是否为空。"
else
    # 准备环境变量用于非交互式创建 (通过 shell -c 传递 Python 代码)
    # 使用 Python Shell 绕过交互式创建，同时检查用户是否已存在
    PYTHON_COMMAND="
from django.contrib.auth import get_user_model;
User = get_user_model();
if not User.objects.filter(username='$SUPERUSER_USER').exists():
    User.objects.create_superuser(username='$SUPERUSER_USER', email='$SUPERUSER_EMAIL', password='$SUPERUSER_PASS');
    print('Superuser created successfully: $SUPERUSER_USER');
else:
    print('Superuser $SUPERUSER_USER already exists, skipping creation.');
"
    # 执行命令：通过 sh -c 运行 python manage.py shell
    if docker compose $COMPOSE_FLAGS run --rm --no-deps web sh -c "echo \"$PYTHON_COMMAND\" | python manage.py shell"; then
        echo "✅ 超级管理员 '$SUPERUSER_USER' 自动化创建流程完成。"
    else
        echo "❌ 自动化创建超级管理员失败，请检查部署日志。"
        exit 1
    fi
fi


# --- 10. 输出环境访问信息 ---
echo "🔥 [Step 7] 启动所有服务..."
echo "================================================"
echo "   部署完成！"

CSRF_PORT_SUFFIX=""
if [[ "$MODE" == "test" ]]; then
    CSRF_PORT_SUFFIX=":8080"
fi

echo "   模式: $MODE"
echo "   访问地址: http://${INPUT_DOMAIN}${CSRF_PORT_SUFFIX}/admin"
echo "   服务状态检查命令: docker compose $COMPOSE_FLAGS ps"
echo "================================================"