#!/bin/bash
#===============================================================================
# vaultctl 설치 스크립트
# 
# 사용법:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/vaultctl/main/scripts/install.sh | sudo bash
#   
#   # 특정 버전
#   curl -fsSL ... | sudo VAULTCTL_VERSION=0.1.0 bash
#   
#   # APT 저장소 사용
#   curl -fsSL ... | sudo VAULTCTL_USE_APT=1 bash
#===============================================================================

set -e

# 기본 설정 - 이 값을 GitHub 저장소에 맞게 수정하세요
GITHUB_OWNER="${GITHUB_OWNER:-your-username}"
GITHUB_REPO="${GITHUB_REPO:-vaultctl}"
VERSION="${VAULTCTL_VERSION:-latest}"
USE_APT="${VAULTCTL_USE_APT:-0}"

# 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
cat << 'EOF'
           _ _       _   _ 
 __ ____ _| | | __ _| |_| |
 \ V / _` | | |/ _` |  _| |
  \_/\__,_|_|_|\__,_|\__|_|
                           
EOF
echo -e "${NC}"
echo "HashiCorp Vault CLI with 1Password integration"
echo ""

#-------------------------------------------------------------------------------
# OS 및 권한 확인
#-------------------------------------------------------------------------------
check_requirements() {
    # root 확인
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}✗ root 권한이 필요합니다.${NC}"
        echo "  curl -fsSL ... | sudo bash"
        exit 1
    fi
    
    # OS 확인
    if [[ ! -f /etc/debian_version ]]; then
        echo -e "${RED}✗ Ubuntu/Debian만 지원합니다.${NC}"
        exit 1
    fi
    
    # 아키텍처 확인
    ARCH=$(dpkg --print-architecture)
    if [[ "$ARCH" != "amd64" ]]; then
        echo -e "${RED}✗ amd64만 지원합니다. (현재: $ARCH)${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓${NC} 시스템 요구사항 확인 완료"
}

#-------------------------------------------------------------------------------
# APT 저장소로 설치
#-------------------------------------------------------------------------------
install_via_apt() {
    echo -e "${YELLOW}APT 저장소 설정 중...${NC}"
    
    APT_URL="https://${GITHUB_OWNER}.github.io/${GITHUB_REPO}"
    
    echo "deb [trusted=yes] $APT_URL stable main" > /etc/apt/sources.list.d/vaultctl.list
    
    apt-get update
    apt-get install -y vaultctl
    
    echo -e "${GREEN}✓${NC} APT로 설치 완료"
}

#-------------------------------------------------------------------------------
# GitHub Releases에서 직접 설치
#-------------------------------------------------------------------------------
install_via_releases() {
    # 최신 버전 조회
    if [[ "$VERSION" == "latest" ]]; then
        echo "최신 버전 확인 중..."
        VERSION=$(curl -fsSL "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest" | \
            grep '"tag_name":' | sed -E 's/.*"v([^"]+)".*/\1/')
        
        if [[ -z "$VERSION" ]]; then
            echo -e "${RED}✗ 최신 버전을 조회할 수 없습니다.${NC}"
            echo "  VAULTCTL_VERSION=0.1.0 으로 직접 지정하세요."
            exit 1
        fi
    fi
    
    echo -e "${BLUE}버전: v$VERSION${NC}"
    
    DEB_NAME="vaultctl_${VERSION}_amd64.deb"
    DEB_URL="https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/download/v${VERSION}/${DEB_NAME}"
    TMP_DEB="/tmp/${DEB_NAME}"
    
    echo "다운로드 중: $DEB_URL"
    
    if ! curl -fsSL -o "$TMP_DEB" "$DEB_URL"; then
        echo -e "${RED}✗ 다운로드 실패${NC}"
        echo "  URL: $DEB_URL"
        exit 1
    fi
    
    echo "설치 중..."
    dpkg -i "$TMP_DEB" || apt-get install -f -y
    
    rm -f "$TMP_DEB"
    
    echo -e "${GREEN}✓${NC} 설치 완료"
}

#-------------------------------------------------------------------------------
# 설치 후 안내
#-------------------------------------------------------------------------------
post_install() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  설치 완료!${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    if [[ ! -f /etc/vaultctl/env ]]; then
        echo "다음 단계:"
        echo ""
        echo "  1. 초기 설정 마법사 실행:"
        echo -e "     ${GREEN}sudo vaultctl setup init${NC}"
        echo ""
        echo "  또는 수동 설정:"
        echo "     sudo cp /etc/vaultctl/env.example /etc/vaultctl/env"
        echo "     sudo chmod 600 /etc/vaultctl/env"
        echo "     sudo nano /etc/vaultctl/env"
        echo "     sudo systemctl enable --now vaultctl-renew.timer"
    else
        echo -e "${GREEN}✓${NC} 업데이트 완료"
        echo ""
        echo "버전 확인: vaultctl --version"
    fi
    echo ""
}

#-------------------------------------------------------------------------------
# 메인
#-------------------------------------------------------------------------------
main() {
    check_requirements
    
    if [[ "$USE_APT" == "1" ]]; then
        install_via_apt
    else
        install_via_releases
    fi
    
    post_install
}

main
