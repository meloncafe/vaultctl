#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# 간단한 APT 저장소 설정 스크립트
# 
# Nginx/Caddy로 정적 파일 서빙하는 방식의 간단한 APT 저장소 생성
# 
# 사용법:
#   # 저장소 서버에서
#   ./setup-apt-repo.sh init
#   ./setup-apt-repo.sh add vaultctl_0.1.0_amd64.deb
#   ./setup-apt-repo.sh update
#
#   # 클라이언트에서
#   ./setup-apt-repo.sh client https://apt.example.com
# ─────────────────────────────────────────────────────────────────────────────

set -e

# 설정
REPO_DIR="${REPO_DIR:-/var/www/apt-repo}"
REPO_NAME="${REPO_NAME:-internal}"
CODENAME="${CODENAME:-stable}"
COMPONENT="${COMPONENT:-main}"
ARCH="${ARCH:-amd64}"

# 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ─────────────────────────────────────────────────────────────────────────────
# 저장소 초기화
# ─────────────────────────────────────────────────────────────────────────────
cmd_init() {
    echo -e "${YELLOW}APT 저장소 초기화...${NC}"
    
    # 디렉토리 생성
    mkdir -p "$REPO_DIR/pool/$COMPONENT"
    mkdir -p "$REPO_DIR/dists/$CODENAME/$COMPONENT/binary-$ARCH"
    
    # conf 디렉토리
    mkdir -p "$REPO_DIR/conf"
    
    # distributions 파일
    cat > "$REPO_DIR/conf/distributions" << EOF
Origin: $REPO_NAME
Label: $REPO_NAME
Codename: $CODENAME
Architectures: $ARCH
Components: $COMPONENT
Description: Internal APT Repository
EOF

    echo -e "${GREEN}✓${NC} 저장소 초기화 완료: $REPO_DIR"
    echo ""
    echo "다음 단계:"
    echo "  1. 패키지 추가: $0 add <package.deb>"
    echo "  2. 인덱스 업데이트: $0 update"
    echo "  3. Nginx/Caddy로 $REPO_DIR 서빙"
}

# ─────────────────────────────────────────────────────────────────────────────
# 패키지 추가
# ─────────────────────────────────────────────────────────────────────────────
cmd_add() {
    local deb_file="$1"
    
    if [[ -z "$deb_file" ]]; then
        echo -e "${RED}✗ 사용법: $0 add <package.deb>${NC}"
        exit 1
    fi
    
    if [[ ! -f "$deb_file" ]]; then
        echo -e "${RED}✗ 파일을 찾을 수 없습니다: $deb_file${NC}"
        exit 1
    fi
    
    # 패키지 복사
    cp "$deb_file" "$REPO_DIR/pool/$COMPONENT/"
    
    echo -e "${GREEN}✓${NC} 패키지 추가: $(basename "$deb_file")"
    echo "  인덱스 업데이트: $0 update"
}

# ─────────────────────────────────────────────────────────────────────────────
# 인덱스 업데이트
# ─────────────────────────────────────────────────────────────────────────────
cmd_update() {
    echo -e "${YELLOW}인덱스 업데이트 중...${NC}"
    
    cd "$REPO_DIR"
    
    # Packages 파일 생성
    apt-ftparchive packages "pool/$COMPONENT" > "dists/$CODENAME/$COMPONENT/binary-$ARCH/Packages"
    gzip -kf "dists/$CODENAME/$COMPONENT/binary-$ARCH/Packages"
    
    # Release 파일 생성
    apt-ftparchive release "dists/$CODENAME" > "dists/$CODENAME/Release"
    
    echo -e "${GREEN}✓${NC} 인덱스 업데이트 완료"
    
    # 패키지 목록 출력
    echo ""
    echo "등록된 패키지:"
    ls -la "$REPO_DIR/pool/$COMPONENT/"*.deb 2>/dev/null || echo "  (없음)"
}

# ─────────────────────────────────────────────────────────────────────────────
# GPG 서명 (선택)
# ─────────────────────────────────────────────────────────────────────────────
cmd_sign() {
    local gpg_key="${1:-}"
    
    if [[ -z "$gpg_key" ]]; then
        echo -e "${YELLOW}GPG 키 없이 진행 (unsigned repository)${NC}"
        return 0
    fi
    
    cd "$REPO_DIR"
    
    # InRelease (clearsign)
    gpg --default-key "$gpg_key" --clearsign -o "dists/$CODENAME/InRelease" "dists/$CODENAME/Release"
    
    # Release.gpg (detached)
    gpg --default-key "$gpg_key" -abs -o "dists/$CODENAME/Release.gpg" "dists/$CODENAME/Release"
    
    # 공개키 내보내기
    gpg --armor --export "$gpg_key" > "$REPO_DIR/KEY.gpg"
    
    echo -e "${GREEN}✓${NC} GPG 서명 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# 클라이언트 설정
# ─────────────────────────────────────────────────────────────────────────────
cmd_client() {
    local repo_url="$1"
    
    if [[ -z "$repo_url" ]]; then
        echo -e "${RED}✗ 사용법: $0 client <repo-url>${NC}"
        echo "  예: $0 client https://apt.example.com"
        exit 1
    fi
    
    echo -e "${YELLOW}APT 클라이언트 설정...${NC}"
    
    # sources.list.d에 추가
    cat << EOF | sudo tee /etc/apt/sources.list.d/vaultctl.list
# vaultctl APT repository
deb [trusted=yes] $repo_url $CODENAME $COMPONENT
EOF

    # 또는 signed repository인 경우
    # curl -fsSL $repo_url/KEY.gpg | sudo gpg --dearmor -o /usr/share/keyrings/vaultctl.gpg
    # echo "deb [signed-by=/usr/share/keyrings/vaultctl.gpg] $repo_url $CODENAME $COMPONENT" | sudo tee /etc/apt/sources.list.d/vaultctl.list

    sudo apt-get update
    
    echo -e "${GREEN}✓${NC} APT 클라이언트 설정 완료"
    echo ""
    echo "설치: sudo apt install vaultctl"
    echo "업데이트: sudo apt update && sudo apt upgrade vaultctl"
}

# ─────────────────────────────────────────────────────────────────────────────
# 클라이언트 설정 제거
# ─────────────────────────────────────────────────────────────────────────────
cmd_client_remove() {
    sudo rm -f /etc/apt/sources.list.d/vaultctl.list
    sudo rm -f /usr/share/keyrings/vaultctl.gpg
    sudo apt-get update
    
    echo -e "${GREEN}✓${NC} APT 클라이언트 설정 제거 완료"
}

# ─────────────────────────────────────────────────────────────────────────────
# Nginx 설정 예시 출력
# ─────────────────────────────────────────────────────────────────────────────
cmd_nginx() {
    cat << 'EOF'
# /etc/nginx/sites-available/apt-repo

server {
    listen 80;
    server_name apt.example.com;
    
    root /var/www/apt-repo;
    
    location / {
        autoindex on;
    }
    
    # Packages 파일 압축 해제 서빙
    location ~ /(.*)/binary-(.*)/Packages$ {
        default_type text/plain;
    }
}
EOF
}

# ─────────────────────────────────────────────────────────────────────────────
# Caddy 설정 예시 출력
# ─────────────────────────────────────────────────────────────────────────────
cmd_caddy() {
    cat << 'EOF'
# /etc/caddy/Caddyfile 추가

apt.example.com {
    root * /var/www/apt-repo
    file_server browse
}
EOF
}

# ─────────────────────────────────────────────────────────────────────────────
# 도움말
# ─────────────────────────────────────────────────────────────────────────────
cmd_help() {
    cat << EOF
APT 저장소 관리 스크립트

사용법: $0 <command> [args]

서버 명령어:
  init              저장소 초기화
  add <file.deb>    패키지 추가
  update            인덱스 업데이트
  sign [gpg-key]    GPG 서명 (선택)
  nginx             Nginx 설정 예시
  caddy             Caddy 설정 예시

클라이언트 명령어:
  client <url>      APT 소스 추가
  client-remove     APT 소스 제거

환경변수:
  REPO_DIR          저장소 디렉토리 (기본: /var/www/apt-repo)
  CODENAME          배포판 코드명 (기본: stable)
  COMPONENT         컴포넌트 (기본: main)

예시:
  # 서버에서
  $0 init
  $0 add vaultctl_0.1.0_amd64.deb
  $0 update
  
  # 클라이언트에서
  $0 client https://apt.internal.example.com
  sudo apt install vaultctl
EOF
}

# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
case "${1:-help}" in
    init)
        cmd_init
        ;;
    add)
        cmd_add "$2"
        ;;
    update)
        cmd_update
        ;;
    sign)
        cmd_sign "$2"
        ;;
    client)
        cmd_client "$2"
        ;;
    client-remove)
        cmd_client_remove
        ;;
    nginx)
        cmd_nginx
        ;;
    caddy)
        cmd_caddy
        ;;
    help|--help|-h)
        cmd_help
        ;;
    *)
        echo -e "${RED}알 수 없는 명령어: $1${NC}"
        cmd_help
        exit 1
        ;;
esac
