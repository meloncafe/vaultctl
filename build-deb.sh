#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# vaultctl deb Package Build Script
# vaultctl deb 패키지 빌드 스크립트
#
# Requirements / 요구사항:
#   - Python 3.10+
#   - Poetry
#   - fpm (gem install fpm)
#   - PyInstaller (installed via poetry)
#
# Usage / 사용법:
#   ./build-deb.sh [version]
#   ./build-deb.sh 0.1.0
# ─────────────────────────────────────────────────────────────────────────────

set -e

# Colors / 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Version / 버전
VERSION="${1:-$(grep 'version = ' pyproject.toml | head -1 | cut -d'"' -f2)}"
PACKAGE_NAME="vaultctl"
ARCH="amd64"
MAINTAINER="vaultctl maintainers"
DESCRIPTION="HashiCorp Vault CLI with 1Password integration"
URL="https://github.com/yourorg/vaultctl"

# Build directories / 빌드 디렉토리
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
PKG_ROOT="$BUILD_DIR/pkg-root"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  vaultctl deb Package Build / deb 패키지 빌드${NC}"
echo -e "${BLUE}  Version: $VERSION${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Check dependencies / 의존성 확인
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
        echo -e "${RED}✗ Missing required tools / 필요한 도구가 없습니다:${NC}"
        for dep in "${missing[@]}"; do
            echo "  - $dep"
        done
        exit 1
    fi
    
    echo -e "${GREEN}✓${NC} Dependency check passed / 의존성 확인 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Clean build directories / 빌드 디렉토리 정리
# ─────────────────────────────────────────────────────────────────────────────
clean() {
    echo -e "${YELLOW}Cleaning build directories... / 빌드 디렉토리 정리 중...${NC}"
    rm -rf "$BUILD_DIR" "$DIST_DIR"
    mkdir -p "$BUILD_DIR" "$DIST_DIR" "$PKG_ROOT"
}

# ─────────────────────────────────────────────────────────────────────────────
# Install Poetry dependencies / Poetry 의존성 설치
# ─────────────────────────────────────────────────────────────────────────────
install_deps() {
    echo -e "${YELLOW}Installing dependencies... / 의존성 설치 중...${NC}"
    poetry install --with dev
    echo -e "${GREEN}✓${NC} Dependencies installed / 의존성 설치 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Build binary with PyInstaller / PyInstaller로 바이너리 빌드
# ─────────────────────────────────────────────────────────────────────────────
build_binary() {
    echo -e "${YELLOW}Building binary... / 바이너리 빌드 중...${NC}"
    
    # Run PyInstaller / PyInstaller 실행
    poetry run pyinstaller \
        --clean \
        --noconfirm \
        --distpath "$BUILD_DIR/dist" \
        --workpath "$BUILD_DIR/work" \
        --specpath "$BUILD_DIR" \
        vaultctl.spec
    
    # Verify binary exists / 바이너리 존재 확인
    if [[ ! -f "$BUILD_DIR/dist/vaultctl" ]]; then
        echo -e "${RED}✗ Binary build failed / 바이너리 빌드 실패${NC}"
        exit 1
    fi
    
    # Set executable permission / 실행 권한 설정
    chmod +x "$BUILD_DIR/dist/vaultctl"
    
    echo -e "${GREEN}✓${NC} Binary built / 바이너리 빌드 완료: $BUILD_DIR/dist/vaultctl"
    
    # Binary info / 바이너리 정보
    ls -lh "$BUILD_DIR/dist/vaultctl"
    file "$BUILD_DIR/dist/vaultctl"
}

# ─────────────────────────────────────────────────────────────────────────────
# Create package structure / 패키지 구조 생성
# ─────────────────────────────────────────────────────────────────────────────
create_package_structure() {
    echo -e "${YELLOW}Creating package structure... / 패키지 구조 생성 중...${NC}"
    
    # Create directories / 디렉토리 생성
    mkdir -p "$PKG_ROOT/usr/bin"
    mkdir -p "$PKG_ROOT/etc/vaultctl"
    mkdir -p "$PKG_ROOT/lib/systemd/system"
    mkdir -p "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME"
    
    # Binary / 바이너리
    cp "$BUILD_DIR/dist/vaultctl" "$PKG_ROOT/usr/bin/"
    chmod +x "$PKG_ROOT/usr/bin/vaultctl"
    
    # Config example from template / 템플릿에서 설정 예제 복사
    if [[ -f "$SCRIPT_DIR/packaging/etc/config.example" ]]; then
        cp "$SCRIPT_DIR/packaging/etc/config.example" "$PKG_ROOT/etc/vaultctl/"
        echo -e "${GREEN}✓${NC} Copied template: packaging/etc/config.example"
    else
        echo -e "${YELLOW}Warning: packaging/etc/config.example not found${NC}"
    fi
    
    # systemd unit files / systemd 유닛 파일
    cp "$SCRIPT_DIR/packaging/systemd/vaultctl-renew.service" "$PKG_ROOT/lib/systemd/system/"
    cp "$SCRIPT_DIR/packaging/systemd/vaultctl-renew.timer" "$PKG_ROOT/lib/systemd/system/"
    
    # Documentation / 문서
    cp "$SCRIPT_DIR/README.md" "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/"
    
    # Changelog (Debian policy) / 변경 이력 (Debian 정책)
    cat > "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/changelog" << EOF
$PACKAGE_NAME ($VERSION) stable; urgency=medium

  * Release $VERSION
  * Token auto-renewal via systemd timer

 -- $MAINTAINER  $(date -R)
EOF
    gzip -9 "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/changelog"
    
    echo -e "${GREEN}✓${NC} Package structure created / 패키지 구조 생성 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Copy maintainer scripts from templates / 템플릿에서 maintainer 스크립트 복사
# ─────────────────────────────────────────────────────────────────────────────
copy_maintainer_scripts() {
    echo -e "${YELLOW}Copying maintainer scripts... / Maintainer 스크립트 복사 중...${NC}"
    
    mkdir -p "$BUILD_DIR/scripts"
    
    local template_dir="$SCRIPT_DIR/packaging/scripts"
    
    if [[ -d "$template_dir" ]]; then
        for script in preinst postinst prerm postrm; do
            if [[ -f "$template_dir/$script" ]]; then
                cp "$template_dir/$script" "$BUILD_DIR/scripts/"
                chmod +x "$BUILD_DIR/scripts/$script"
                echo -e "${GREEN}✓${NC} Copied: $script"
            else
                echo -e "${YELLOW}Warning: $template_dir/$script not found${NC}"
            fi
        done
        echo -e "${GREEN}✓${NC} Maintainer scripts copied / Maintainer 스크립트 복사 완료"
    else
        echo -e "${RED}✗ Template directory not found: $template_dir${NC}"
        echo -e "${YELLOW}Creating inline scripts as fallback... / 폴백으로 인라인 스크립트 생성...${NC}"
        create_inline_scripts
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Fallback: Create inline maintainer scripts / 폴백: 인라인 maintainer 스크립트 생성
# ─────────────────────────────────────────────────────────────────────────────
create_inline_scripts() {
    mkdir -p "$BUILD_DIR/scripts"
    
    # preinst
    cat > "$BUILD_DIR/scripts/preinst" << 'EOF'
#!/bin/bash
set -e
if [ "$1" = "upgrade" ] || [ "$1" = "install" ]; then
    rm -f /lib/systemd/system/vaultctl-renew.service
    rm -f /lib/systemd/system/vaultctl-renew.timer
fi
exit 0
EOF

    # postinst
    cat > "$BUILD_DIR/scripts/postinst" << 'EOF'
#!/bin/bash
set -e
if command -v systemctl &> /dev/null; then
    systemctl daemon-reload
    systemctl reset-failed vaultctl-renew.service 2>/dev/null || true
    systemctl reset-failed vaultctl-renew.timer 2>/dev/null || true
    if [[ -f /etc/vaultctl/env ]]; then
        systemctl enable vaultctl-renew.timer 2>/dev/null || true
        systemctl start vaultctl-renew.timer 2>/dev/null || true
    fi
fi
if [[ ! -f /etc/vaultctl/env ]]; then
    echo "Run 'sudo vaultctl setup init' to configure"
fi
exit 0
EOF

    # prerm
    cat > "$BUILD_DIR/scripts/prerm" << 'EOF'
#!/bin/bash
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v systemctl &> /dev/null; then
        systemctl stop vaultctl-renew.timer 2>/dev/null || true
        systemctl disable vaultctl-renew.timer 2>/dev/null || true
    fi
fi
exit 0
EOF

    # postrm
    cat > "$BUILD_DIR/scripts/postrm" << 'EOF'
#!/bin/bash
set -e
if command -v systemctl &> /dev/null; then
    systemctl daemon-reload
fi
[[ "$1" = "purge" ]] && rm -rf /etc/vaultctl
exit 0
EOF

    chmod +x "$BUILD_DIR/scripts/"*
    echo -e "${GREEN}✓${NC} Inline scripts created / 인라인 스크립트 생성 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Build deb package with fpm / fpm으로 deb 패키지 빌드
# ─────────────────────────────────────────────────────────────────────────────
build_deb() {
    echo -e "${YELLOW}Building deb package... / deb 패키지 생성 중...${NC}"
    
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
        --vendor "vaultctl" \
        --category "admin" \
        --depends "ca-certificates" \
        --config-files /etc/vaultctl/config.example \
        --before-install "$BUILD_DIR/scripts/preinst" \
        --after-install "$BUILD_DIR/scripts/postinst" \
        --before-remove "$BUILD_DIR/scripts/prerm" \
        --after-remove "$BUILD_DIR/scripts/postrm" \
        --package "$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb" \
        --chdir "$PKG_ROOT" \
        .
    
    echo -e "${GREEN}✓${NC} deb package created / deb 패키지 생성 완료"
    ls -lh "$DIST_DIR/"*.deb
}

# ─────────────────────────────────────────────────────────────────────────────
# Verify package / 패키지 검증
# ─────────────────────────────────────────────────────────────────────────────
verify_package() {
    echo -e "${YELLOW}Verifying package... / 패키지 검증 중...${NC}"
    
    DEB_FILE="$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    
    echo ""
    echo "Package info / 패키지 정보:"
    dpkg-deb --info "$DEB_FILE"
    
    echo ""
    echo "Package contents / 패키지 내용:"
    dpkg-deb --contents "$DEB_FILE"
    
    echo ""
    echo -e "${GREEN}✓${NC} Package verified / 패키지 검증 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Main / 메인
# ─────────────────────────────────────────────────────────────────────────────
main() {
    check_deps
    clean
    install_deps
    build_binary
    create_package_structure
    copy_maintainer_scripts
    build_deb
    verify_package
    
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Build complete! / 빌드 완료!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "Package / 패키지: $DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo ""
    echo "Install / 설치:"
    echo "  sudo dpkg -i $DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "  # or / 또는"
    echo "  sudo apt install ./$DIST_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
}

main
