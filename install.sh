#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# vaultctl 설치 스크립트
# 용도: vaultctl 패키지 및 systemd 서비스 설치
# ─────────────────────────────────────────────────────────────────────────────

set -e

# 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"
}

# 설정
PYPI_URL="${PYPI_URL:-}"  # 내부 PyPI URL (비어있으면 공식 PyPI)
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-true}"

print_header "vaultctl 설치"

# ─────────────────────────────────────────────────────────────────────────────
# pipx 설치 확인
# ─────────────────────────────────────────────────────────────────────────────
if ! command -v pipx &> /dev/null; then
    echo -e "${YELLOW}![/yellow] pipx가 설치되지 않았습니다. 설치 중...${NC}"
    
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y pipx
        pipx ensurepath
    elif command -v brew &> /dev/null; then
        brew install pipx
        pipx ensurepath
    else
        python3 -m pip install --user pipx
        python3 -m pipx ensurepath
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# vaultctl 설치
# ─────────────────────────────────────────────────────────────────────────────
echo "vaultctl 설치 중..."

if [[ -n "$PYPI_URL" ]]; then
    pipx install vaultctl --index-url "$PYPI_URL"
else
    # 로컬 설치 (개발용)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        pipx install "$SCRIPT_DIR"
    else
        echo -e "${RED}✗${NC} PYPI_URL을 설정하거나 프로젝트 디렉토리에서 실행하세요."
        exit 1
    fi
fi

echo -e "${GREEN}✓${NC} vaultctl 설치 완료"

# ─────────────────────────────────────────────────────────────────────────────
# systemd 서비스 설치 (선택)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$INSTALL_SYSTEMD" == "true" ]] && [[ -d /etc/systemd/system ]]; then
    print_header "systemd 서비스 설치"
    
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SYSTEMD_DIR="$SCRIPT_DIR/systemd"
    
    if [[ -d "$SYSTEMD_DIR" ]]; then
        # 서비스 파일 복사
        sudo cp "$SYSTEMD_DIR/vaultctl-renew.service" /etc/systemd/system/
        sudo cp "$SYSTEMD_DIR/vaultctl-renew.timer" /etc/systemd/system/
        
        # 환경변수 파일 디렉토리 생성
        sudo mkdir -p /etc/vaultctl
        if [[ ! -f /etc/vaultctl/env ]]; then
            sudo cp "$SYSTEMD_DIR/env.example" /etc/vaultctl/env
            sudo chmod 600 /etc/vaultctl/env
            echo -e "${YELLOW}![/yellow] /etc/vaultctl/env 파일을 수정하세요"
        fi
        
        # systemd 리로드
        sudo systemctl daemon-reload
        
        echo -e "${GREEN}✓${NC} systemd 서비스 설치 완료"
        echo ""
        echo "타이머 활성화:"
        echo "  sudo systemctl enable --now vaultctl-renew.timer"
        echo ""
        echo "상태 확인:"
        echo "  systemctl status vaultctl-renew.timer"
        echo "  systemctl list-timers | grep vaultctl"
    else
        echo -e "${YELLOW}![/yellow] systemd 파일을 찾을 수 없습니다: $SYSTEMD_DIR"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 완료
# ─────────────────────────────────────────────────────────────────────────────
print_header "설치 완료"

echo "다음 단계:"
echo "  1. 터미널 재시작 또는: source ~/.bashrc"
echo "  2. 1Password 로그인: eval \$(op signin)"
echo "  3. Vault 인증: vaultctl auth login"
echo "  4. 테스트: vaultctl auth status"
echo ""
echo "도움말: vaultctl --help"
