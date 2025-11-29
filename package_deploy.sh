#!/bin/bash
# 文件路径: package_deploy.sh
# 描述: VSS Cloud 部署包构建工具 (Tarball 版)
# 功能: 将部署所需的核心文件打包成 tar.gz，用于分发到生产服务器。
# 运行: ./package_deploy.sh [版本号]

set -e

# --- 1. 配置 ---
APP_NAME="vss-cloud"
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")
DEFAULT_VERSION="$(date +%Y%m%d)-${GIT_HASH}"
VERSION=${1:-$DEFAULT_VERSION}

OUTPUT_DIR="dist"
PACKAGE_NAME="${APP_NAME}-deploy-${VERSION}"
TAR_FILE="${PACKAGE_NAME}.tar.gz"
TEMP_DIR="${OUTPUT_DIR}/${PACKAGE_NAME}"

# --- 2. 定义交付物清单 (Manifest) ---
FILES_TO_COPY=(
    "init.sh"
    "install_deps.sh"
    ".env.template"
    "docker-compose.base.yml"
    "docker-compose.test.yml"
    "docker-compose.prod.yml"
    "conf/nginx.template.conf"
    "conf/gcp-credentials.json"
    "README.md"
)

# --- 3. 清理与初始化 ---
echo "📦 开始构建部署包: ${TAR_FILE} ..."
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"
mkdir -p "$TEMP_DIR/conf"

# 在打包阶段直接创建生产环境的数据挂载目录
# 这样解压后，目录结构就是完整的，无需 init.sh 再去 mkdir
echo "   📂 创建生产数据目录结构..."
mkdir -p "$TEMP_DIR/prod_data/postgres"
mkdir -p "$TEMP_DIR/prod_data/redis"

# 为了防止 tar 在某些特殊参数下忽略空文件夹，
# 或者是为了 Git 仓库也能保留这个结构，我们可以放一个空的占位文件
touch "$TEMP_DIR/prod_data/postgres/.keep"
touch "$TEMP_DIR/prod_data/redis/.keep"

# --- 4. 复制文件 ---
echo "📋 正在复制文件..."
MISSING_CRITICAL=0

for file in "${FILES_TO_COPY[@]}"; do
    if [ -f "$file" ]; then
        # 使用 cp --parents 保持目录结构
        cp --parents "$file" "$TEMP_DIR/"
        echo "   ✅ Included: $file"
    else
        echo "   ⚠️  Warning: 关键文件 '$file' 未找到！"
        if [[ "$file" == "init.sh" || "$file" == ".env.template" ]]; then
             MISSING_CRITICAL=1
        fi
    fi
done

if [ $MISSING_CRITICAL -eq 1 ]; then
    echo "❌ Error: 无法继续，缺失核心依赖文件 (init.sh 或 .env.template)。"
    read -n 1 -s -r -p "按任意键退出..."
    exit 1
fi

# --- 5. 生成说明 ---
echo "📝 生成部署说明..."
cat > "$TEMP_DIR/DEPLOY_NOTES.txt" <<EOF
VSS Cloud Deployment Package
Version: ${VERSION}
Built at: $(date)

部署步骤:
1. 解压: tar -zxvf ${TAR_FILE}
2. 进入目录: cd ${PACKAGE_NAME}
3. 环境准备 (仅首次): sudo ./install_deps.sh
4. 退出重新登录以生效 Docker 权限。
5. 初始化配置: ./init.sh --prod
EOF

# --- 6. 打包 (使用 tar) ---
echo "🗜️  正在压缩 (tar.gz)..."
cd "$OUTPUT_DIR"

# tar -czvf filename.tar.gz directory/
if tar -czf "${TAR_FILE}" "${PACKAGE_NAME}"; then
    echo "✅ 打包成功！"
    echo "   📁 文件位置: ${OUTPUT_DIR}/${TAR_FILE}"
    # 尝试获取文件大小 (兼容 Linux du 和 Mac du)
    FILE_SIZE=$(du -h "${TAR_FILE}" | awk '{print $1}')
    echo "   📦 文件大小: ${FILE_SIZE}"
else
    echo "❌ Error: tar 打包失败。"
    cd .. # 回到根目录
    read -n 1 -s -r -p "按任意键退出..."
    exit 1
fi

# 清理临时目录
rm -rf "${PACKAGE_NAME}"

echo "========================================"
echo "🎉 构建完成。请将 dist/${TAR_FILE} 上传至服务器。"
echo "========================================"

# --- 7. 结束暂停 ---
read -n 1 -s -r -p "✅ 按任意键关闭窗口..."
echo ""