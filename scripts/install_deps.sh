#!/bin/bash
# 文件路径: install_deps.sh
# 描述: VSS Cloud 服务器环境初始化脚本
# 功能: 自动检测 OS，安装 Docker Engine & Compose，配置用户权限。
# 支持: Ubuntu, Debian, CentOS/RHEL

set -e

# --- 颜色定义 ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO] $1${NC}"; }
log_warn() { echo -e "${YELLOW}[WARN] $1${NC}"; }
log_err() { echo -e "${RED}[ERROR] $1${NC}"; }

# --- 1. 检查 Root 权限 ---
if [ "$(id -u)" != "0" ]; then
   log_err "此脚本需要 Root 权限运行。"
   log_info "请尝试: sudo ./install_deps.sh"
   exit 1
fi

# --- 2. 检测操作系统 ---
log_info "正在检测操作系统..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    log_err "无法检测操作系统 (未找到 /etc/os-release)。"
    exit 1
fi

log_info "检测到操作系统: $OS"

# --- 3. 安装 Docker ---
install_docker() {
    if command -v docker >/dev/null 2>&1; then
        log_warn "Docker 已安装，跳过安装步骤。"
        return
    fi

    log_info "开始安装 Docker Engine..."

    case $OS in
        ubuntu|debian)
            # 移除旧版本
            for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
                apt-get remove -y $pkg || true
            done

            # 更新并安装依赖
            apt-get update
            apt-get install -y ca-certificates curl gnupg

            # 添加 GPG Key
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            chmod a+r /etc/apt/keyrings/docker.gpg

            # 设置仓库
            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
              $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
              tee /etc/apt/sources.list.d/docker.list > /dev/null

            # 安装
            apt-get update
            apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;

        centos|rhel)
            # 移除旧版本
            yum remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine || true

            # 安装 yum-utils
            yum install -y yum-utils

            # 设置仓库
            yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

            # 安装
            yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

            # 启动 Docker
            systemctl start docker
            systemctl enable docker
            ;;

        *)
            log_err "不支持的操作系统: $OS"
            log_err "请参考 Docker 官方文档手动安装: https://docs.docker.com/engine/install/"
            exit 1
            ;;
    esac

    log_info "Docker 安装完成。"
}

install_docker

# --- 4. 配置用户权限 (免 sudo) ---
SUDO_USER=${SUDO_USER:-$(whoami)}

if [ "$SUDO_USER" != "root" ]; then
    log_info "正在将用户 '$SUDO_USER' 添加到 docker 用户组..."

    # 创建组（如果不存在）
    groupadd docker 2>/dev/null || true

    # 添加用户
    usermod -aG docker "$SUDO_USER"

    log_info "权限配置完成。"
    log_warn "⚠️  注意: 您需要【重新登录】服务器，或者运行 'newgrp docker' 命令，才能使组权限生效。"
else
    log_warn "当前直接以 root 用户运行，跳过用户组配置。"
fi

# --- 5. 验证安装 ---
log_info "正在验证安装..."
docker --version
docker compose version

log_info "========================================"
log_info "✅ 环境准备完毕！"
log_info "接下来您可以上传部署包并运行 init.sh 了。"
log_info "========================================"