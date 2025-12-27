#!/bin/bash
#===============================================================================
# 개인 APT 저장소 서버 구축 스크립트 (Traefik 환경용)
# 
# 구성:
#   - reprepro: APT 저장소 관리 + GPG 서명
#   - Nginx: 정적 파일 서빙 (Traefik 뒤에서)
#   - Basic Auth: 선택적 인증
#
# 전제:
#   - Traefik이 앞단에서 Let's Encrypt 인증서 관리
#   - Traefik → 이 LXC:80 으로 라우팅
#
# 사용법:
#   export DOMAIN="apt.example.com"
#   sudo ./setup-apt-server-traefik.sh
#===============================================================================

set -e

# ═══════════════════════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════════════════════

# APT 저장소 설정
REPO_NAME="${REPO_NAME:-internal}"
REPO_LABEL="${REPO_LABEL:-Internal Repository}"
REPO_CODENAME="${REPO_CODENAME:-stable}"
REPO_ARCH="${REPO_ARCH:-amd64}"
REPO_COMPONENTS="${REPO_COMPONENTS:-main}"

# 디렉토리
REPO_BASE="/var/www/apt"
REPO_DIR="$REPO_BASE/repo"
GPG_HOME="$REPO_BASE/.gnupg"

# GPG 설정
GPG_NAME="${GPG_NAME:-APT Repository Signing Key}"
GPG_EMAIL="${GPG_EMAIL:-apt@example.com}"
GPG_EXPIRE="${GPG_EXPIRE:-0}"  # 0 = 무기한

# 도메인 (Traefik에서 라우팅)
DOMAIN="${DOMAIN:-apt.example.com}"

# 인증 설정
ENABLE_AUTH="${ENABLE_AUTH:-true}"
AUTH_USER="${AUTH_USER:-apt}"
AUTH_PASS="${AUTH_PASS:-}"  # 비어있으면 자동 생성

# Nginx 리스닝 포트 (Traefik이 이 포트로 프록시)
LISTEN_PORT="${LISTEN_PORT:-80}"

# 색상
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# 패키지 설치
# ═══════════════════════════════════════════════════════════════════════════════

install_packages() {
    print_header "패키지 설치"
    
    apt-get update
    apt-get install -y \
        reprepro \
        gnupg \
        gnupg-agent \
        nginx \
        apache2-utils \
        curl
    
    echo -e "${GREEN}✓${NC} 패키지 설치 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 디렉토리 구조 생성
# ═══════════════════════════════════════════════════════════════════════════════

setup_directories() {
    print_header "디렉토리 구조 생성"
    
    mkdir -p "$REPO_DIR"/{conf,db,dists,pool}
    mkdir -p "$GPG_HOME"
    
    chmod 700 "$GPG_HOME"
    chown -R www-data:www-data "$REPO_BASE"
    
    echo -e "${GREEN}✓${NC} 디렉토리 생성 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# GPG 키 생성 (패키지 서명용 - HTTPS와 별개)
# ═══════════════════════════════════════════════════════════════════════════════

setup_gpg() {
    print_header "GPG 서명 키 설정"
    
    echo -e "${YELLOW}참고: 이 GPG 키는 패키지 서명용입니다.${NC}"
    echo -e "${YELLOW}      HTTPS 인증서(Traefik/Let's Encrypt)와는 별개입니다.${NC}"
    echo ""
    
    export GNUPGHOME="$GPG_HOME"
    
    # 기존 키 확인
    if gpg --list-keys "$GPG_EMAIL" &>/dev/null; then
        echo -e "${GREEN}✓${NC} 기존 GPG 키 발견: $GPG_EMAIL"
        GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT "$GPG_EMAIL" 2>/dev/null | grep -oP '(?<=rsa)[0-9]+/\K[A-F0-9]+' | head -1)
    else
        echo "GPG 키 생성 중..."
        
        # 엔트로피 생성 (LXC에서 필요할 수 있음)
        apt-get install -y rng-tools 2>/dev/null || true
        
        cat > /tmp/gpg-batch << EOF
%echo Generating APT signing key
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: $GPG_NAME
Name-Email: $GPG_EMAIL
Expire-Date: $GPG_EXPIRE
%no-protection
%commit
%echo Done
EOF
        
        gpg --batch --gen-key /tmp/gpg-batch
        rm /tmp/gpg-batch
        
        echo -e "${GREEN}✓${NC} GPG 키 생성 완료"
    fi
    
    # Key ID 추출
    GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT "$GPG_EMAIL" 2>/dev/null | grep -E '^\s+[A-F0-9]+' | awk '{print $1}' | head -1)
    
    if [[ -z "$GPG_KEY_ID" ]]; then
        # 다른 방식으로 시도
        GPG_KEY_ID=$(gpg --list-keys --keyid-format LONG "$GPG_EMAIL" 2>/dev/null | grep -oP '[A-F0-9]{16}' | head -1)
        GPG_KEY_ID="${GPG_KEY_ID: -8}"  # 마지막 8자리
    fi
    
    echo "  Key ID: $GPG_KEY_ID"
    
    # 공개키 내보내기
    gpg --armor --export "$GPG_EMAIL" > "$REPO_DIR/KEY.gpg"
    gpg --export "$GPG_EMAIL" > "$REPO_DIR/KEY"
    
    # 환경 변수로 저장 (다른 함수에서 사용)
    export GPG_KEY_ID
    
    echo -e "${GREEN}✓${NC} 공개키 내보내기: $REPO_DIR/KEY.gpg"
}

# ═══════════════════════════════════════════════════════════════════════════════
# reprepro 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_reprepro() {
    print_header "reprepro 설정"
    
    export GNUPGHOME="$GPG_HOME"
    
    # distributions 설정
    cat > "$REPO_DIR/conf/distributions" << EOF
Origin: $REPO_NAME
Label: $REPO_LABEL
Codename: $REPO_CODENAME
Architectures: $REPO_ARCH
Components: $REPO_COMPONENTS
Description: $REPO_LABEL
SignWith: $GPG_KEY_ID
EOF

    # options 설정
    cat > "$REPO_DIR/conf/options" << EOF
verbose
basedir $REPO_DIR
gnupghome $GPG_HOME
ask-passphrase
EOF

    echo -e "${GREEN}✓${NC} reprepro 설정 완료"
    
    # 초기화
    cd "$REPO_DIR"
    reprepro export
    
    echo -e "${GREEN}✓${NC} 저장소 초기화 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 인증 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_auth() {
    print_header "인증 설정"
    
    if [[ "$ENABLE_AUTH" != "true" ]]; then
        echo -e "${YELLOW}!${NC} 인증 비활성화 (공개 저장소)"
        AUTH_ENABLED="false"
        return
    fi
    
    # 비밀번호 자동 생성
    if [[ -z "$AUTH_PASS" ]]; then
        AUTH_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
    fi
    
    # htpasswd 파일 생성
    htpasswd -bc "$REPO_BASE/.htpasswd" "$AUTH_USER" "$AUTH_PASS"
    chmod 600 "$REPO_BASE/.htpasswd"
    chown www-data:www-data "$REPO_BASE/.htpasswd"
    
    AUTH_ENABLED="true"
    
    echo -e "${GREEN}✓${NC} 인증 설정 완료"
    echo ""
    echo -e "${YELLOW}┌─────────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│  인증 정보 (안전하게 보관하세요!)                       │${NC}"
    echo -e "${YELLOW}├─────────────────────────────────────────────────────────┤${NC}"
    echo -e "${YELLOW}│  사용자: $AUTH_USER${NC}"
    echo -e "${YELLOW}│  비밀번호: $AUTH_PASS${NC}"
    echo -e "${YELLOW}└─────────────────────────────────────────────────────────┘${NC}"
    
    # 인증 정보 파일 저장
    cat > "$REPO_BASE/.credentials" << EOF
# APT 저장소 인증 정보
USER=$AUTH_USER
PASS=$AUTH_PASS
URL=https://$DOMAIN
EOF
    chmod 600 "$REPO_BASE/.credentials"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Nginx 설정 (Traefik 뒤에서 동작)
# ═══════════════════════════════════════════════════════════════════════════════

setup_nginx() {
    print_header "Nginx 설정 (Traefik 백엔드)"
    
    # 기본 사이트 비활성화
    rm -f /etc/nginx/sites-enabled/default
    
    # APT 저장소 설정
    if [[ "$AUTH_ENABLED" == "true" ]]; then
        cat > /etc/nginx/sites-available/apt-repo << EOF
server {
    listen $LISTEN_PORT;
    server_name $DOMAIN;
    
    root $REPO_DIR;
    
    # 공개 접근 허용 (GPG 키, 설치 스크립트)
    location ~ ^/(KEY\.gpg|KEY|add-key\.sh|setup-client\.sh|index\.html)$ {
        allow all;
    }
    
    # 나머지는 인증 필요
    location / {
        auth_basic "APT Repository";
        auth_basic_user_file $REPO_BASE/.htpasswd;
        
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }
    
    # 로그
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}
EOF
    else
        cat > /etc/nginx/sites-available/apt-repo << EOF
server {
    listen $LISTEN_PORT;
    server_name $DOMAIN;
    
    root $REPO_DIR;
    
    location / {
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }
    
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}
EOF
    fi
    
    ln -sf /etc/nginx/sites-available/apt-repo /etc/nginx/sites-enabled/
    
    # 설정 테스트 및 재시작
    nginx -t
    systemctl enable nginx
    systemctl restart nginx
    
    echo -e "${GREEN}✓${NC} Nginx 설정 완료 (포트 $LISTEN_PORT)"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 관리 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_management_scripts() {
    print_header "관리 스크립트 생성"
    
    # apt-repo-add
    cat > /usr/local/bin/apt-repo-add << 'EOF'
#!/bin/bash
set -e
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

DEB_FILE="$1"
CODENAME="${2:-stable}"

if [[ -z "$DEB_FILE" ]] || [[ ! -f "$DEB_FILE" ]]; then
    echo "사용법: apt-repo-add <package.deb> [codename]"
    exit 1
fi

echo "패키지 추가: $DEB_FILE"
dpkg-deb --info "$DEB_FILE" | grep -E '^ (Package|Version|Architecture):'

cd "$REPO_DIR"
reprepro includedeb "$CODENAME" "$DEB_FILE"

echo "✓ 완료"
EOF
    chmod +x /usr/local/bin/apt-repo-add
    
    # apt-repo-remove
    cat > /usr/local/bin/apt-repo-remove << 'EOF'
#!/bin/bash
set -e
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

PACKAGE="$1"
CODENAME="${2:-stable}"

if [[ -z "$PACKAGE" ]]; then
    echo "사용법: apt-repo-remove <package-name> [codename]"
    exit 1
fi

cd "$REPO_DIR"
reprepro remove "$CODENAME" "$PACKAGE"
echo "✓ 제거 완료: $PACKAGE"
EOF
    chmod +x /usr/local/bin/apt-repo-remove
    
    # apt-repo-list
    cat > /usr/local/bin/apt-repo-list << 'EOF'
#!/bin/bash
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME
cd "$REPO_DIR"
reprepro list "${1:-stable}"
EOF
    chmod +x /usr/local/bin/apt-repo-list
    
    echo -e "${GREEN}✓${NC} 관리 스크립트 생성 완료"
    echo "  apt-repo-add / apt-repo-remove / apt-repo-list"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 클라이언트 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_client_scripts() {
    print_header "클라이언트 스크립트 생성"
    
    # 클라이언트 설정 스크립트
    cat > "$REPO_DIR/setup-client.sh" << 'CLIENTEOF'
#!/bin/bash
set -e

DOMAIN="__DOMAIN__"
AUTH_USER="${1:-}"
AUTH_PASS="${2:-}"
CODENAME="__CODENAME__"

echo "APT 저장소 클라이언트 설정..."

# 1. GPG 키 추가
echo "1. GPG 키 추가..."
if [[ -n "$AUTH_USER" ]]; then
    curl -fsSL -u "$AUTH_USER:$AUTH_PASS" "https://$DOMAIN/KEY.gpg" | \
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
else
    curl -fsSL "https://$DOMAIN/KEY.gpg" | \
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
fi

# 2. 인증 설정
if [[ -n "$AUTH_USER" ]]; then
    echo "2. 인증 설정..."
    mkdir -p /etc/apt/auth.conf.d
    cat > /etc/apt/auth.conf.d/internal.conf << EOF
machine $DOMAIN
login $AUTH_USER
password $AUTH_PASS
EOF
    chmod 600 /etc/apt/auth.conf.d/internal.conf
fi

# 3. APT 소스 추가
echo "3. APT 소스 추가..."
cat > /etc/apt/sources.list.d/internal.list << EOF
deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://$DOMAIN $CODENAME main
EOF

# 4. 업데이트
echo "4. 업데이트..."
apt-get update

echo ""
echo "✓ 완료! 이제 설치할 수 있습니다:"
echo "  sudo apt install vaultctl"
CLIENTEOF

    sed -i "s/__DOMAIN__/$DOMAIN/g" "$REPO_DIR/setup-client.sh"
    sed -i "s/__CODENAME__/$REPO_CODENAME/g" "$REPO_DIR/setup-client.sh"
    chmod +x "$REPO_DIR/setup-client.sh"
    
    # 간단한 인덱스 페이지
    cat > "$REPO_DIR/index.html" << EOF
<!DOCTYPE html>
<html>
<head><title>APT Repository</title></head>
<body>
<h1>Internal APT Repository</h1>
<p><a href="/KEY.gpg">GPG Key</a> | <a href="/setup-client.sh">Setup Script</a></p>
</body>
</html>
EOF
    
    echo -e "${GREEN}✓${NC} 클라이언트 스크립트: https://$DOMAIN/setup-client.sh"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Traefik 설정 안내
# ═══════════════════════════════════════════════════════════════════════════════

print_traefik_config() {
    print_header "Traefik 라우팅 설정"
    
    # LXC IP 가져오기
    LXC_IP=$(hostname -I | awk '{print $1}')
    
    echo "Traefik에 다음 라우팅을 추가하세요:"
    echo ""
    echo -e "${BLUE}Docker labels 방식:${NC}"
    cat << EOF
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.apt-repo.rule=Host(\`$DOMAIN\`)"
  - "traefik.http.routers.apt-repo.entrypoints=websecure"
  - "traefik.http.routers.apt-repo.tls.certresolver=letsencrypt"
  - "traefik.http.services.apt-repo.loadbalancer.server.url=http://$LXC_IP:$LISTEN_PORT"
EOF
    
    echo ""
    echo -e "${BLUE}파일 기반 설정:${NC}"
    cat << EOF
# /etc/traefik/dynamic/apt-repo.yml
http:
  routers:
    apt-repo:
      rule: "Host(\`$DOMAIN\`)"
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      service: apt-repo
  
  services:
    apt-repo:
      loadBalancer:
        servers:
          - url: "http://$LXC_IP:$LISTEN_PORT"
EOF
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# 최종 안내
# ═══════════════════════════════════════════════════════════════════════════════

print_summary() {
    print_header "설치 완료"
    
    LXC_IP=$(hostname -I | awk '{print $1}')
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  저장소 정보"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  URL:        https://$DOMAIN"
    echo "  내부 IP:    $LXC_IP:$LISTEN_PORT"
    echo "  저장소:     $REPO_DIR"
    echo ""
    
    if [[ "$AUTH_ENABLED" == "true" ]]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  인증 정보"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "  사용자:     $AUTH_USER"
        echo "  비밀번호:   $AUTH_PASS"
        echo ""
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  다음 단계"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  1. Traefik에 라우팅 추가 (위 설정 참고)"
    echo ""
    echo "  2. 패키지 추가:"
    echo "     apt-repo-add vaultctl_0.1.0_amd64.deb"
    echo ""
    echo "  3. 클라이언트 설정:"
    if [[ "$AUTH_ENABLED" == "true" ]]; then
        echo "     curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash -s -- $AUTH_USER '$AUTH_PASS'"
    else
        echo "     curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════════════

main() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}✗ root 권한이 필요합니다.${NC}"
        exit 1
    fi
    
    print_header "APT 저장소 구축 (Traefik 환경)"
    
    install_packages
    setup_directories
    setup_gpg
    setup_reprepro
    setup_auth
    setup_nginx
    create_management_scripts
    create_client_scripts
    print_traefik_config
    print_summary
}

main "$@"
