#!/bin/bash
# 文件路径: package_deploy.sh
# 描述: VSS Cloud 部署包构建工具 (Tarball 版)
# 功能: 将部署所需的核心文件打包成 tar.gz，支持指定环境（test/prod）选择对应yml，兼容多系统
# 运行: ./package_deploy.sh [版本号] [--env test|prod] (默认prod)

set -e

# --- 跨系统兼容处理：适配Mac/Linux的readlink/realpath差异 ---
if [[ "$(uname)" == "Darwin" ]]; then
    READLINK="greadlink"
    # 检查是否安装coreutils（Mac下提供greadlink）
    if ! command -v $READLINK &> /dev/null; then
        echo "[ERROR] MacOS需要安装coreutils (brew install coreutils)"
        exit 1
    fi
else
    READLINK="readlink"
fi

# 统一获取脚本目录（兼容所有系统）
SCRIPT_DIR=$($READLINK -f "$(dirname "${BASH_SOURCE[0]}")")
PROJECT_ROOT=$(dirname "$SCRIPT_DIR") # 初始计算项目根目录
# 校验根目录有效性（必须包含核心文件）
cd "$PROJECT_ROOT" || { echo "[ERROR] 无法进入项目根目录: $PROJECT_ROOT"; exit 1; }
if [ ! -f ".env.template" ] || [ ! -f "docker-compose.base.yml" ]; then
    echo "[ERROR] $PROJECT_ROOT 不是有效项目根目录（缺失核心文件 .env.template/docker-compose.base.yml）"
    exit 1
fi
echo "[INFO] 工作目录已设定为: $(pwd)"

# --- 1. 配置与参数解析 ---
APP_NAME="vss-cloud"
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")
DEFAULT_VERSION="$(date +%Y%m%d)-${GIT_HASH}"
TARGET_ENV="prod" # 默认生产环境
# 预定义核心文件数组（用于后续校验）
CRITICAL_FILES=("scripts/init.sh" ".env.template" "docker-compose.base.yml" "docker-compose.${TARGET_ENV}.yml")

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            if [[ "$2" == "test" || "$2" == "prod" ]]; then
                TARGET_ENV="$2"
                # 更新核心文件数组中的环境专属yml
                CRITICAL_FILES[3]="docker-compose.${TARGET_ENV}.yml"
                shift 2
            else
                echo "[ERROR] 无效的环境参数: $2，仅支持 test/prod"
                exit 1
            fi
            ;;
        --help|-h)
            echo "使用帮助:"
            echo "  ./package_deploy.sh [版本号] [--env test|prod]"
            echo "  版本号: 可选，默认格式 年月日-Git哈希"
            echo "  --env: 可选，指定打包环境（test/prod），默认prod"
            echo "示例:"
            echo "  生产环境 + 默认版本: ./package_deploy.sh"
            echo "  测试环境 + 指定版本: ./package_deploy.sh v1.0.0 --env test"
            exit 0
            ;;
        *)
            # 版本号参数
            if [[ -z "$VERSION" ]]; then
                VERSION="$1"
                shift
            else
                echo "[ERROR] 未知参数: $1"
                echo "使用 --help 查看帮助"
                exit 1
            fi
            ;;
    esac
done

# 版本号默认值
VERSION=${VERSION:-$DEFAULT_VERSION}

# 核心路径配置
OUTPUT_DIR="dist"
PACKAGE_NAME="${APP_NAME}-deploy-${VERSION}-${TARGET_ENV}"
TAR_FILE="${PACKAGE_NAME}.tar.gz"
TEMP_DIR="${OUTPUT_DIR}/${PACKAGE_NAME}"

# --- 2. 定义交付物清单 (Manifest) ---
# 移除README.md，根据环境选择对应yml
FILES_TO_COPY=(
    "scripts/install_deps.sh"
    "scripts/init_setup.sh"
    "scripts/init_exec.sh"
    ".env.template"
    "docker-compose.base.yml"
    "docker-compose.${TARGET_ENV}.yml"
    "conf/nginx.template.conf"
    "conf/gcp-credentials.json"
    "DeployInstruction.md"
)

# --- 3. 清理与初始化 ---
echo "[INFO] 开始构建${TARGET_ENV}环境部署包: ${TAR_FILE}"
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"
mkdir -p "$TEMP_DIR/conf"

# 保留原有目录结构（不修改，避免联动docker-compose.yml）
echo "[INFO] 创建${TARGET_ENV}数据目录结构..."
mkdir -p "$TEMP_DIR/${TARGET_ENV}_data/postgres"
mkdir -p "$TEMP_DIR/${TARGET_ENV}_data/redis"

# 空目录占位文件（防止tar忽略，设置可读权限）
touch "$TEMP_DIR/${TARGET_ENV}_data/postgres/.keep" && chmod 644 "$_"
touch "$TEMP_DIR/${TARGET_ENV}_data/redis/.keep" && chmod 644 "$_"

# --- 4. 复制文件 (兼容Ubuntu/Debian/Alpine) ---
echo "[INFO] 正在复制文件..."
MISSING_CRITICAL=0

for file in "${FILES_TO_COPY[@]}"; do
    if [ -f "$file" ]; then
        # scripts目录下文件扁平化到根目录
        if [[ "$file" == scripts/* ]]; then
            cp "$file" "$TEMP_DIR/"
            echo "[INFO] Included (Flattened): $file -> root"
        else
            # 其他文件保持目录结构
            cp --parents "$file" "$TEMP_DIR/"
            echo "[INFO] Included: $file"
        fi
    else
        echo "[WARN] 文件 '$file' 未找到！"
        # 核心文件校验（数组包含判断，简化逻辑）
        if [[ " ${CRITICAL_FILES[*]} " =~ " $file " ]]; then
            MISSING_CRITICAL=1
        fi
    fi
done

if [ $MISSING_CRITICAL -eq 1 ]; then
    echo "[ERROR] 无法继续，缺失核心依赖文件！"
    exit 1
fi

# --- 5. 生成环境专属部署说明 ---
echo "[INFO] 生成${TARGET_ENV}环境部署说明..."
cat > "$TEMP_DIR/DEPLOY_NOTES.txt" <<EOF
VSS Cloud Deployment Package
Version: ${VERSION}
Environment: ${TARGET_ENV}
Built at: $(date)
Built on: $(uname -s) $(uname -r)

部署步骤:
1. 解压: tar -zxvf ${TAR_FILE}
2. 进入目录: cd ${PACKAGE_NAME}
3. 环境准备 (仅首次): sudo ./install_deps.sh
4. 退出重新登录或执行 newgrp docker 使 Docker 权限生效。
5. 初始化配置 (交互式): ./init_setup.sh --${TARGET_ENV}
6. 启动部署 (自动化): sudo ./init_exec.sh
EOF

# --- 6. 打包 (兼容Mac/Linux tar参数差异) ---
echo "[INFO] 正在压缩 (tar.gz)..."
cd "$OUTPUT_DIR" || { echo "[ERROR] 无法进入输出目录: $OUTPUT_DIR"; exit 1; }

# 系统兼容的tar打包命令
if [[ "$(uname)" == "Darwin" ]]; then
    # MacOS (BSD tar) 不支持--owner/--group参数
    tar -czf "${TAR_FILE}" "${PACKAGE_NAME}"
else
    # Linux (GNU tar) 强制设置属主/属组为root
    tar -czf "${TAR_FILE}" --owner=0 --group=0 "${PACKAGE_NAME}"
fi

# 校验打包结果
if [ -f "${TAR_FILE}" ]; then
    echo "[INFO] 打包成功！"
    echo "[INFO] 文件位置: ${OUTPUT_DIR}/${TAR_FILE}"
    # 兼容Linux/Mac的du命令获取文件大小
    if [[ "$(uname)" == "Darwin" ]]; then
        FILE_SIZE=$(du -h "${TAR_FILE}" | awk '{print $1}')
    else
        FILE_SIZE=$(du -h "${TAR_FILE}" | cut -f1)
    fi
    echo "[INFO] 文件大小: ${FILE_SIZE}"
else
    echo "[ERROR] tar 打包失败。"
    cd "$PROJECT_ROOT" || exit 1
    exit 1
fi

# 清理临时目录
rm -rf "${PACKAGE_NAME}"

echo "========================================"
echo "[SUCCESS] ${TARGET_ENV}环境部署包构建完成！"
echo "[INFO] 上传文件: dist/${TAR_FILE}"
echo "========================================"

# 非交互环境跳过暂停（兼容CI/CD）
if [[ -t 1 ]]; then
    read -n 1 -s -r -p "[INFO] 按任意键关闭窗口..."
    echo ""
fi