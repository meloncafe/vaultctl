#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# vaultctl deb 패키지 빌드 스크립트
# 
# 요구사항:
#   - Python 3.10+
#   - Poetry
#   - fpm (gem install fpm)
#   - PyInstaller (poetry install로 설치됨)
#
# 사용법:
#   ./build-deb.sh [version]
#   ./build-deb.sh 0.1.0
# ─────────────────────────────────────────────────────────────────────────────

set -e

# 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 버전
VERSION="${1:-$(grep 'version = ' pyproject.toml | head -1 | cut -d'"' -f2)}"
PACKAGE_NAME="vaultctl"
ARCH="amd64"
MAINTAINER="Aether <aether@example.com>"
DESCRIPTION="HashiCorp Vault CLI with 1Password integration"
URL="https://github.com/yourorg/vaultctl"

# 빌드 디렉토리
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
PKG_ROOT="$BUILD_DIR/pkg-root"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  vaultctl deb 패키지 빌드${NC}"
echo -e "${BLUE}  Version: $VERSION${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 의존성 확인
# ─────────────────────────────────────────────────────────────────────────────
check_deps() {
    local missing=()
    
    if ! command -v python3 &> /dev/null; then
        missing+=("python3")
    fi
    
    if ! command -v poetry &> /dev/null; then
        missing+=("poetry")
    fi
    
    if ! command -v fpm &> /dev/null; then
        missing+=("fpm (gem install fpm)")
    fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}✗ 필요한 도구가 없습니다:${NC}"
        for dep in "${missing[@]}"; do
            echo "  - $dep"
        done
        exit 1
    fi
    
    echo -e "${GREEN}✓${NC} 의존성 확인 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# 정리
# ─────────────────────────────────────────────────────────────────────────────
clean() {
    echo -e "${YELLOW}정리 중...${NC}"
    rm -rf "$BUILD_DIR" "$DIST_DIR"
    mkdir -p "$BUILD_DIR" "$DIST_DIR" "$PKG_ROOT"
}

# ─────────────────────────────────────────────────────────────────────────────
# Poetry 의존성 설치
# ─────────────────────────────────────────────────────────────────────────────
install_deps() {
    echo -e "${YELLOW}의존성 설치 중...${NC}"
    poetry install --with dev
    echo -e "${GREEN}✓${NC} 의존성 설치 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# PyInstaller로 바이너리 빌드
# ─────────────────────────────────────────────────────────────────────────────
build_binary() {
    echo -e "${YELLOW}바이너리 빌드 중...${NC}"
    
    # PyInstaller 실행
    poetry run pyinstaller \
        --clean \
        --noconfirm \
        --distpath "$BUILD_DIR/dist" \
        --workpath "$BUILD_DIR/work" \
        --specpath "$BUILD_DIR" \
        vaultctl.spec
    
    # 바이너리 확인
    if [[ ! -f "$BUILD_DIR/dist/vaultctl" ]]; then
        echo -e "${RED}✗ 바이너리 빌드 실패${NC}"
        exit 1
    fi
    
    # 실행 권한
    chmod +x "$BUILD_DIR/dist/vaultctl"
    
    echo -e "${GREEN}✓${NC} 바이너리 빌드 완료: $BUILD_DIR/dist/vaultctl"
    
    # 바이너리 정보
    ls -lh "$BUILD_DIR/dist/vaultctl"
    file "$BUILD_DIR/dist/vaultctl"
}

# ─────────────────────────────────────────────────────────────────────────────
# 패키지 구조 생성
# ─────────────────────────────────────────────────────────────────────────────
create_package_structure() {
    echo -e "${YELLOW}패키지 구조 생성 중...${NC}"
    
    # 디렉토리 생성
    mkdir -p "$PKG_ROOT/usr/bin"
    mkdir -p "$PKG_ROOT/etc/vaultctl"
    mkdir -p "$PKG_ROOT/lib/systemd/system"
    mkdir -p "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME"
    
    # 바이너리 복사
    cp "$BUILD_DIR/dist/vaultctl" "$PKG_ROOT/usr/bin/"
    
    # 설정 파일
    cat > "$PKG_ROOT/etc/vaultctl/config.env.example" << 'EOF'
# vaultctl 설정 파일
# 이 파일을 config.env로 복사하고 수정하세요
# cp /etc/vaultctl/config.env.example /etc/vaultctl/config.env

# Vault 설정
VAULTCTL_VAULT_ADDR=https://vault.example.com
VAULTCTL_KV_MOUNT=proxmox
VAULTCTL_KV_LXC_PATH=lxc
VAULTCTL_KV_DOCKER_PATH=docker

# 1Password 설정 (데스크탑 사용 시)
VAULTCTL_OP_VAULT=Infrastructure
VAULTCTL_OP_ITEM=vault-token
VAULTCTL_OP_FIELD=credential

# 토큰 갱신 설정
VAULTCTL_TOKEN_RENEW_THRESHOLD=3600
VAULTCTL_TOKEN_RENEW_INCREMENT=86400
EOF

    # systemd 서비스용 환경 파일
    cat > "$PKG_ROOT/etc/vaultctl/env.example" << 'EOF'
# systemd 서비스용 환경 파일
# 이 파일을 env로 복사하고 토큰을 설정하세요
# cp /etc/vaultctl/env.example /etc/vaultctl/env
# chmod 600 /etc/vaultctl/env

VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=hvs.xxxxxxxxxxxxx

VAULTCTL_VAULT_ADDR=https://vault.example.com
VAULTCTL_TOKEN_RENEW_THRESHOLD=3600
VAULTCTL_TOKEN_RENEW_INCREMENT=86400
EOF

    # systemd 서비스
    cat > "$PKG_ROOT/lib/systemd/system/vaultctl-renew.service" << 'EOF'
[Unit]
Description=Vault Token Auto Renew
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/vaultctl token auto-renew --quiet
EnvironmentFile=-/etc/vaultctl/env

# 보안 설정
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
EOF

    # systemd 타이머
    cat > "$PKG_ROOT/lib/systemd/system/vaultctl-renew.timer" << 'EOF'
[Unit]
Description=Vault Token Auto Renew Timer

[Timer]
# 매시간 실행
OnCalendar=hourly
# 부팅 후 5분 뒤 첫 실행
OnBootSec=5min
# 놓친 실행 복구
Persistent=true
# 랜덤 지연 (동시 실행 방지)
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

    # 문서
    cp README.md "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/"
    
    # changelog
    cat > "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/changelog" << EOF
vaultctl ($VERSION) stable; urgency=medium

  * Initial release
  * 1Password CLI integration
  * LXC secrets management
  * Docker environment variable management
  * Token auto-renewal via systemd timer

 -- $MAINTAINER  $(date -R)
EOF
    gzip -9 "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/changelog"
    
    echo -e "${GREEN}✓${NC} 패키지 구조 생성 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# postinst 스크립트 생성
# ─────────────────────────────────────────────────────────────────────────────
create_scripts() {
    mkdir -p "$BUILD_DIR/scripts"
    
    # preinst (설치 전) - 기존 systemd 파일 정리
    cat > "$BUILD_DIR/scripts/preinst" << 'EOF'
#!/bin/bash
set -e

# 업그레이드 시 기존 systemd 파일 삭제 (새 버전으로 교체 보장)
if [ "$1" = "upgrade" ] || [ "$1" = "install" ]; then
    rm -f /lib/systemd/system/vaultctl-renew.service
    rm -f /lib/systemd/system/vaultctl-renew.timer
fi

exit 0
EOF

    # postinst (설치 후)
    cat > "$BUILD_DIR/scripts/postinst" << 'EOF'
#!/bin/bash
set -e

# systemd 리로드 및 실패 상태 초기화
if command -v systemctl &> /dev/null; then
    systemctl daemon-reload
    systemctl reset-failed vaultctl-renew.service 2>/dev/null || true
    systemctl reset-failed vaultctl-renew.timer 2>/dev/null || true
    
    # timer가 활성화되어 있으면 서비스 한 번 실행 (업그레이드 후 정상 작동 확인)
    if systemctl is-enabled vaultctl-renew.timer &>/dev/null; then
        systemctl start vaultctl-renew.service 2>/dev/null || true
    fi
fi

# 설정 파일 생성 안내
if [[ ! -f /etc/vaultctl/env ]]; then
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║  vaultctl installed successfully!                         ║"
    echo "╠═══════════════════════════════════════════════════════════╣"
    echo "║  Quick setup:                                             ║"
    echo "║    sudo vaultctl setup init                               ║"
    echo "║                                                           ║"
    echo "║  Or manual:                                               ║"
    echo "║    sudo cp /etc/vaultctl/env.example /etc/vaultctl/env    ║"
    echo "║    sudo chmod 600 /etc/vaultctl/env                       ║"
    echo "║    sudo nano /etc/vaultctl/env                            ║"
    echo "║    sudo systemctl enable --now vaultctl-renew.timer       ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
fi

exit 0
EOF

    # prerm (제거 전)
    cat > "$BUILD_DIR/scripts/prerm" << 'EOF'
#!/bin/bash
set -e

# systemd 서비스 중지
if command -v systemctl &> /dev/null; then
    systemctl stop vaultctl-renew.timer 2>/dev/null || true
    systemctl disable vaultctl-renew.timer 2>/dev/null || true
fi

exit 0
EOF

    # postrm (제거 후)
    cat > "$BUILD_DIR/scripts/postrm" << 'EOF'
#!/bin/bash
set -e

# systemd 리로드
if command -v systemctl &> /dev/null; then
    systemctl daemon-reload
fi

# purge 시 설정 파일 삭제
if [[ "$1" = "purge" ]]; then
    rm -rf /etc/vaultctl
fi

exit 0
EOF

    chmod +x "$BUILD_DIR/scripts/"*
}

# ─────────────────────────────────────────────────────────────────────────────
# fpm으로 deb 패키지 생성
# ─────────────────────────────────────────────────────────────────────────────
build_deb() {
    echo -e "${YELLOW}deb 패키지 생성 중...${NC}"
    
    fpm \
        --input-type dir \
        --output-type deb \
        --name "$PACKAGE_NAME" \
        --version "$VERSION" \
        --architecture "$ARCH" \
        --maintainer "$MAINTAINER" \
        --description "$DESCRIPTION" \
        --url "$URL" \
        --license "MIT" \
        --vendor "Aether" \
        --category "admin" \
        --depends "ca-certificates" \
        --config-files /etc/vaultctl/config.env.example \
        --config-files /etc/vaultctl/env.example \
        --before-install "$BUILD_DIR/scripts/preinst" \
        --after-install "$BUILD_DIR/scripts/postinst" \
        --before-remove "$BUILD_DIR/scripts/prerm" \
        --after-remove "$BUILD_DIR/scripts/postrm" \
        --package "$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb" \
        --chdir "$PKG_ROOT" \
        .
    
    echo -e "${GREEN}✓${NC} deb 패키지 생성 완료"
    ls -lh "$DIST_DIR/"*.deb
}

# ─────────────────────────────────────────────────────────────────────────────
# 패키지 검증
# ─────────────────────────────────────────────────────────────────────────────
verify_package() {
    echo -e "${YELLOW}패키지 검증 중...${NC}"
    
    DEB_FILE="$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    
    echo ""
    echo "패키지 정보:"
    dpkg-deb --info "$DEB_FILE"
    
    echo ""
    echo "패키지 내용:"
    dpkg-deb --contents "$DEB_FILE"
    
    echo ""
    echo -e "${GREEN}✓${NC} 패키지 검증 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
main() {
    check_deps
    clean
    install_deps
    build_binary
    create_package_structure
    create_scripts
    build_deb
    verify_package
    
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  빌드 완료!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "패키지: $DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo ""
    echo "설치:"
    echo "  sudo dpkg -i $DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "  # 또는"
    echo "  sudo apt install ./$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
}

main