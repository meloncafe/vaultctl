#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# vaultctl installer - Download and install latest release from GitHub
# vaultctl 설치 스크립트 - GitHub에서 최신 릴리스 다운로드 및 설치
#
# Usage / 사용법:
#   curl -fsSL https://raw.githubusercontent.com/meloncafe/vaultctl/main/scripts/install.sh | sudo bash
#   wget -qO- https://raw.githubusercontent.com/meloncafe/vaultctl/main/scripts/install.sh | sudo bash
#
# Options / 옵션:
#   VERSION=0.1.0 ... | sudo bash   # Install specific version / 특정 버전 설치
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# Configuration / 설정
REPO="${REPO:-meloncafe/vaultctl}"
PACKAGE_NAME="vaultctl"
ARCH="amd64"
TMP_DIR=$(mktemp -d)

# Colors / 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Cleanup on exit / 종료 시 정리
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# Print functions / 출력 함수
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Check root / root 확인
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root. Use: sudo bash install.sh"
    fi
}

# Check dependencies / 의존성 확인
check_deps() {
    local missing=()
    for cmd in curl jq; do
        if ! command -v "$cmd" &> /dev/null; then
            missing+=("$cmd")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        info "Installing missing dependencies: ${missing[*]}"
        apt-get update -qq
        apt-get install -y -qq "${missing[@]}"
    fi
}

# Get latest version from GitHub / GitHub에서 최신 버전 가져오기
get_latest_version() {
    local api_url="https://api.github.com/repos/${REPO}/releases/latest"
    local version
    
    version=$(curl -fsSL "$api_url" | jq -r '.tag_name' | sed 's/^v//')
    
    if [[ -z "$version" || "$version" == "null" ]]; then
        error "Failed to get latest version from GitHub"
    fi
    
    echo "$version"
}

# Download and install / 다운로드 및 설치
install_vaultctl() {
    local version="${VERSION:-$(get_latest_version)}"
    local deb_name="${PACKAGE_NAME}_${version}_${ARCH}.deb"
    local download_url="https://github.com/${REPO}/releases/download/v${version}/${deb_name}"
    
    info "Installing vaultctl v${version}..."
    info "Download URL: ${download_url}"
    
    # Download / 다운로드
    info "Downloading ${deb_name}..."
    if ! curl -fsSL -o "${TMP_DIR}/${deb_name}" "$download_url"; then
        error "Failed to download ${deb_name}"
    fi
    
    # Verify download / 다운로드 확인
    if [[ ! -f "${TMP_DIR}/${deb_name}" ]]; then
        error "Downloaded file not found"
    fi
    
    # Install / 설치
    info "Installing package..."
    apt-get install -y "${TMP_DIR}/${deb_name}"
    
    # Verify installation / 설치 확인
    if command -v vaultctl &> /dev/null; then
        local installed_version
        installed_version=$(vaultctl --version 2>/dev/null || echo "unknown")
        info "Successfully installed vaultctl ${installed_version}"
    else
        error "Installation failed - vaultctl not found in PATH"
    fi
}

# Post-install instructions / 설치 후 안내
show_next_steps() {
    echo ""
    info "Installation complete! Next steps:"
    echo ""
    echo "  1. Initialize configuration:"
    echo "     sudo vaultctl setup init"
    echo ""
    echo "  2. Or manually configure:"
    echo "     sudo cp /etc/vaultctl/config.example /etc/vaultctl/config"
    echo "     sudo nano /etc/vaultctl/config"
    echo ""
    echo "  3. Enable auto token renewal (optional):"
    echo "     sudo systemctl enable --now vaultctl-renew.timer"
    echo ""
    echo "  4. Test connection:"
    echo "     vaultctl setup test"
    echo ""
}

# Main / 메인
main() {
    info "vaultctl installer"
    echo ""
    
    check_root
    check_deps
    install_vaultctl
    show_next_steps
}

main "$@"
