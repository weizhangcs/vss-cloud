#!/bin/bash
# 文件路径: install_deps.sh
# 描述: VSS Cloud 服务器环境初始化脚本
# 功能: 自动/手动指定OS，安装 Docker Engine & Compose，配置用户权限，支持国内外源切换
# 支持: Ubuntu 24.04 LTS (noble), Debian 12 LTS (bookworm)
# 使用方式:
#   海外源 + 自动检测OS: sudo ./install_deps.sh
#   国内源 + 自动检测OS: sudo ./install_deps.sh --cn
#   国内源 + 指定Ubuntu: sudo ./install_deps.sh --cn --os ubuntu

set -e

# --- 颜色定义 ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO] $1${NC}"; }
log_warn() { echo -e "${YELLOW}[WARN] $1${NC}"; }
log_err() { echo -e "${RED}[ERROR] $1${NC}"; exit 1; } # 简化 log_err 后的退出

# --- 默认配置 ---
SOURCE_TYPE="overseas"
TARGET_OS="auto"
# 阿里云源配置 (针对 Ubuntu 24.04 / Debian 12)
ALIYUN_UBUNTU_REPO="https://mirrors.aliyun.com/ubuntu/"
ALIYUN_DEBIAN_REPO="https://mirrors.aliyun.com/debian/"
ALIYUN_DOCKER_REPO="https://mirrors.aliyun.com/docker-ce/linux"
# 官方源配置
OFFICIAL_DOCKER_REPO="https://download.docker.com/linux"

# --- 参数解析 ---
parse_params() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --cn) SOURCE_TYPE="cn"; shift ;;
            --os)
                # 仅支持 ubuntu 或 debian
                if [[ "$2" =~ ^(ubuntu|debian)$ ]]; then
                    TARGET_OS="$2"; shift 2
                else
                    log_err "无效的OS参数: $2，仅支持 ubuntu/debian"
                fi
                ;;
            --help|-h)
                echo "使用帮助:"
                echo "  sudo ./install_deps.sh [--cn] [--os ubuntu|debian]"
                echo "  --cn: 使用国内阿里云源（默认海外官方源）"
                echo "  --os: 指定操作系统（默认自动检测）"
                exit 0
                ;;
            *) log_err "未知参数: $1" ;;
        esac
    done
}

# --- 检查 Root 权限 ---
check_root() {
    if [ "$(id -u)" != "0" ] || [ -z "$SUDO_USER" ]; then
        log_err "必须使用 sudo 以Root权限运行此脚本！"
    fi
    log_info "当前执行用户: $SUDO_USER"
}

# --- 系统检测 & 版本强验证 (聚焦LTS版本) ---
detect_os() {
    if [ ! -f /etc/os-release ]; then
        log_err "无法检测操作系统（未找到 /etc/os-release）"
    fi
    . /etc/os-release

    # 如果手动指定了OS，但实际系统不匹配，则使用实际系统ID，并继续强验证
    if [[ "$TARGET_OS" != "auto" ]] && [[ "$ID" != "$TARGET_OS" ]]; then
        log_warn "手动指定OS($TARGET_OS)与实际系统($ID)不匹配，使用实际系统ID继续"
    fi
    TARGET_OS="$ID"

    case $TARGET_OS in
        ubuntu)
            # 仅支持 Ubuntu 24.04 LTS (Noble)
            [[ "$VERSION_ID" != "24.04" ]] && log_err "仅支持Ubuntu 24.04 LTS"
            OS_VERSION="noble"
            ;;
        debian)
            # 仅支持 Debian 12 LTS (Bookworm)
            [[ "$VERSION_ID" != "12" ]] && log_err "仅支持Debian 12 LTS"
            OS_VERSION="bookworm"
            ;;
        *) log_err "不支持的操作系统: $TARGET_OS (仅支持 Ubuntu 24.04 / Debian 12)" ;;
    esac
    log_info "检测到操作系统: $TARGET_OS $OS_VERSION (符合要求)"
}

# --- 通用源配置 ---
config_common_repo() {
    log_info "配置${SOURCE_TYPE}源..."

    if [ "$SOURCE_TYPE" == "cn" ]; then
        cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true

        # 使用动态的 OS_VERSION 变量
        case $TARGET_OS in
            ubuntu)
                cat > /etc/apt/sources.list << EOF
deb $ALIYUN_UBUNTU_REPO ${OS_VERSION} main restricted universe multiverse
deb $ALIYUN_UBUNTU_REPO ${OS_VERSION}-updates main restricted universe multiverse
deb $ALIYUN_UBUNTU_REPO ${OS_VERSION}-backports main restricted universe multiverse
deb $ALIYUN_UBUNTU_REPO ${OS_VERSION}-security main restricted universe multiverse
EOF
                ;;
            debian)
                cat > /etc/apt/sources.list << EOF
deb $ALIYUN_DEBIAN_REPO ${OS_VERSION} main contrib non-free non-free-firmware
deb $ALIYUN_DEBIAN_REPO ${OS_VERSION}-updates main contrib non-free non-free-firmware
deb $ALIYUN_DEBIAN_REPO ${OS_VERSION}-backports main contrib non-free non-free-firmware
# 安全更新切换到官方源，解决阿里云404问题
deb http://security.debian.org/debian-security ${OS_VERSION}-security main contrib non-free non-free-firmware
EOF
                ;;
        esac
    fi

    # 无论是否修改源，都执行 update
    apt update || log_err "${TARGET_OS}源更新失败"
}

# --- 安装 dos2unix ---
install_dos2unix() {
    log_info "安装dos2unix..."
    apt install -y dos2unix || log_err "dos2unix安装失败"
    log_info "dos2unix安装完成"
}

# --- 安装 Docker (Debian/Ubuntu) ---
# --- 安装 Docker (Debian/Ubuntu) ---
install_docker() {
    if command -v docker &>/dev/null; then
        log_warn "Docker已安装，跳过"; return
    fi
    log_info "安装Docker (Debian/Ubuntu)..."
    local docker_repo=$([ "$SOURCE_TYPE" == "cn" ] && echo "$ALIYUN_DOCKER_REPO" || echo "$OFFICIAL_DOCKER_REPO")
    # 强制使用官方源下载 GPG 密钥
    local official_docker_repo="https://download.docker.com/linux"
    local arch=$(dpkg --print-architecture)

    # 移除旧版本
    apt-get remove -y docker.io docker-doc docker-compose podman-docker containerd runc || true
    # 安装依赖
    apt-get install -y ca-certificates curl gnupg || log_err "Docker依赖安装失败"

    # --- 关键修改：采用 Docker 推荐的密钥分发方式 ---
    log_info "下载并配置Docker官方GPG密钥..."
    install -m 0755 -d /etc/apt/keyrings

    # 1. 尝试使用官方推荐的 keyrings 路径下载密钥
    curl -fsSL "${official_docker_repo}/debian/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg || log_err "下载Docker GPG密钥失败"

    # 2. 将密钥文件权限设置为可读
    chmod a+r /etc/apt/keyrings/docker.gpg

    # --- 添加仓库 (使用新的 signed-by 路径) ---
    # 注意：使用 /etc/apt/keyrings/ 路径，而非 /etc/apt/trusted.gpg.d/
    echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] ${docker_repo}/${TARGET_OS} ${OS_VERSION} stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update || log_err "Docker源更新失败"
    # 安装Docker
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || log_err "Docker核心组件安装失败"
    systemctl enable --now docker
    log_info "Docker安装完成 (Debian/Ubuntu)"
}

# --- 配置Docker权限 ---
config_docker_permission() {
    log_info "添加用户 $SUDO_USER 到docker组..."
    groupadd docker 2>/dev/null || true # 确保组存在
    usermod -aG docker "$SUDO_USER"
    log_warn "⚠️  需重新登录服务器，或执行 newgrp docker 使Docker权限生效！"
}

# --- 验证安装（Debian/Ubuntu 专用版） ---
verify_install() {
    log_info "验证安装结果..."

    # 验证Docker引擎
    if ! docker --version &>/dev/null; then
        log_err "Docker引擎验证失败"
    fi

    # 验证Compose（仅检查新版插件）
    if ! docker compose version &>/dev/null; then
        # 考虑到新版本安装的都是 docker-compose-plugin，主要验证 docker compose 命令
        log_err "Docker Compose (docker compose) 验证失败"
    fi

    # 验证dos2unix
    command -v dos2unix &>/dev/null || log_warn "dos2unix未安装（非核心依赖，但不影响主业务）"
    log_info "✅ 所有核心组件验证通过！"
}

# --- 主流程 ---
main() {
    parse_params "$@"
    check_root
    detect_os
    config_common_repo
    install_dos2unix
    install_docker
    config_docker_permission
    verify_install

    log_info "========================================"
    log_info "🎉 环境初始化完成！"
    log_info "源类型: $SOURCE_TYPE | 系统: $TARGET_OS $OS_VERSION"
    log_info "用户: $SUDO_USER | Docker权限已配置"
    log_info "========================================"
}

# 启动主流程
main "$@"