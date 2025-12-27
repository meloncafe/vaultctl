#!/bin/bash
#===============================================================================
# 개인 APT 저장소 서버 구축 스크립트
# 
# 기능:
#   - reprepro 기반 APT 저장소
#   - GPG 서명 지원
#   - Basic Auth 또는 IP 제한으로 프라이빗 구성
#   - Caddy로 HTTPS 자동 인증서
#
# 사용법:
#   # LXC 내부에서 실행
#   sudo ./setup-apt-server.sh
#
# 요구사항:
#   - Ubuntu 22.04+ LXC
#   - 도메인 (예: apt.example.com)
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
INCOMING_DIR="$REPO_BASE/incoming"
GPG_HOME="$REPO_BASE/.gnupg"

# GPG 설정
GPG_NAME="${GPG_NAME:-APT Repository Signing Key}"
GPG_EMAIL="${GPG_EMAIL:-apt@example.com}"
GPG_EXPIRE="${GPG_EXPIRE:-0}"  # 0 = 무기한

# 웹 서버 설정
DOMAIN="${DOMAIN:-apt.example.com}"
ENABLE_AUTH="${ENABLE_AUTH:-true}"
AUTH_USER="${AUTH_USER:-apt}"
AUTH_PASS="${AUTH_PASS:-}"  # 비어있으면 자동 생성

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
        debian-keyring \
        debian-archive-keyring \
        apt-utils \
        apache2-utils \
        curl \
        jq
    
    # Caddy 설치
    if ! command -v caddy &> /dev/null; then
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
            gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
            tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update
        apt-get install -y caddy
    fi
    
    echo -e "${GREEN}✓${NC} 패키지 설치 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 디렉토리 구조 생성
# ═══════════════════════════════════════════════════════════════════════════════

setup_directories() {
    print_header "디렉토리 구조 생성"
    
    mkdir -p "$REPO_DIR"/{conf,db,dists,pool,incoming}
    mkdir -p "$INCOMING_DIR"
    mkdir -p "$GPG_HOME"
    
    chmod 700 "$GPG_HOME"
    
    # 소유권 설정
    chown -R www-data:www-data "$REPO_BASE"
    
    echo -e "${GREEN}✓${NC} 디렉토리 생성 완료"
    echo "  저장소: $REPO_DIR"
    echo "  수신함: $INCOMING_DIR"
}

# ═══════════════════════════════════════════════════════════════════════════════
# GPG 키 생성
# ═══════════════════════════════════════════════════════════════════════════════

setup_gpg() {
    print_header "GPG 키 설정"
    
    export GNUPGHOME="$GPG_HOME"
    
    # 기존 키 확인
    if gpg --list-keys "$GPG_EMAIL" &>/dev/null; then
        echo -e "${YELLOW}!${NC} 기존 GPG 키 발견: $GPG_EMAIL"
        GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT "$GPG_EMAIL" | grep -oP '(?<=rsa\d{4}\/)[A-F0-9]+')
        echo "  Key ID: $GPG_KEY_ID"
    else
        echo "GPG 키 생성 중... (시간이 걸릴 수 있습니다)"
        
        # 배치 모드로 키 생성
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
        
        GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT "$GPG_EMAIL" | grep -oP '(?<=rsa4096\/)[A-F0-9]+')
        echo -e "${GREEN}✓${NC} GPG 키 생성 완료"
        echo "  Key ID: $GPG_KEY_ID"
    fi
    
    # 공개키 내보내기
    gpg --armor --export "$GPG_EMAIL" > "$REPO_DIR/KEY.gpg"
    gpg --export "$GPG_EMAIL" > "$REPO_DIR/KEY"
    
    # 클라이언트용 스크립트 생성
    cat > "$REPO_DIR/add-key.sh" << 'KEYEOF'
#!/bin/bash
# APT 저장소 GPG 키 추가
curl -fsSL REPO_URL/KEY.gpg | sudo gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
echo "GPG 키가 추가되었습니다."
KEYEOF
    sed -i "s|REPO_URL|https://$DOMAIN|g" "$REPO_DIR/add-key.sh"
    chmod +x "$REPO_DIR/add-key.sh"
    
    echo -e "${GREEN}✓${NC} 공개키 내보내기 완료: $REPO_DIR/KEY.gpg"
}

# ═══════════════════════════════════════════════════════════════════════════════
# reprepro 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_reprepro() {
    print_header "reprepro 설정"
    
    export GNUPGHOME="$GPG_HOME"
    GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT "$GPG_EMAIL" | grep -oP '(?<=rsa4096\/)[A-F0-9]+' | head -1)
    
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

    # incoming 설정 (자동 처리용)
    cat > "$REPO_DIR/conf/incoming" << EOF
Name: default
IncomingDir: $INCOMING_DIR
TempDir: /tmp
Allow: $REPO_CODENAME
Cleanup: on_deny on_error
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
        echo -e "${YELLOW}!${NC} 인증 비활성화됨 (공개 저장소)"
        return
    fi
    
    # 비밀번호 자동 생성
    if [[ -z "$AUTH_PASS" ]]; then
        AUTH_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
    fi
    
    # htpasswd 파일 생성
    htpasswd -bc "$REPO_BASE/.htpasswd" "$AUTH_USER" "$AUTH_PASS"
    chmod 600 "$REPO_BASE/.htpasswd"
    
    echo -e "${GREEN}✓${NC} 인증 설정 완료"
    echo ""
    echo -e "${YELLOW}중요: 아래 정보를 안전하게 보관하세요${NC}"
    echo "  사용자: $AUTH_USER"
    echo "  비밀번호: $AUTH_PASS"
    
    # 인증 정보 파일 저장
    cat > "$REPO_BASE/.credentials" << EOF
# APT 저장소 인증 정보
# 이 파일을 안전하게 보관하세요!

USER=$AUTH_USER
PASS=$AUTH_PASS
URL=https://$DOMAIN

# 클라이언트 설정:
# echo "machine $DOMAIN login $AUTH_USER password $AUTH_PASS" | sudo tee -a /etc/apt/auth.conf.d/internal.conf
# sudo chmod 600 /etc/apt/auth.conf.d/internal.conf
EOF
    chmod 600 "$REPO_BASE/.credentials"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Caddy 웹 서버 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_caddy() {
    print_header "Caddy 웹 서버 설정"
    
    # Caddy 설정
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        cat > /etc/caddy/Caddyfile << EOF
$DOMAIN {
    root * $REPO_DIR
    
    # 공개 파일 (GPG 키, 설치 스크립트)
    @public {
        path /KEY.gpg /KEY /add-key.sh /index.html
    }
    handle @public {
        file_server
    }
    
    # 나머지는 인증 필요
    handle {
        basic_auth {
            $AUTH_USER $(caddy hash-password --plaintext "$AUTH_PASS")
        }
        file_server browse
    }
    
    # 로그
    log {
        output file /var/log/caddy/apt-access.log
    }
}
EOF
    else
        cat > /etc/caddy/Caddyfile << EOF
$DOMAIN {
    root * $REPO_DIR
    file_server browse
    
    log {
        output file /var/log/caddy/apt-access.log
    }
}
EOF
    fi
    
    # 로그 디렉토리
    mkdir -p /var/log/caddy
    
    # Caddy 재시작
    systemctl enable caddy
    systemctl restart caddy
    
    echo -e "${GREEN}✓${NC} Caddy 설정 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 관리 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_management_scripts() {
    print_header "관리 스크립트 생성"
    
    # apt-repo-add: 패키지 추가
    cat > /usr/local/bin/apt-repo-add << 'EOF'
#!/bin/bash
# APT 저장소에 패키지 추가
# 사용법: apt-repo-add <package.deb> [codename]

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

cd "$REPO_DIR"

# 패키지 정보 출력
echo "패키지 추가 중: $DEB_FILE"
dpkg-deb --info "$DEB_FILE" | grep -E '^ (Package|Version|Architecture):'

# reprepro로 추가
reprepro includedeb "$CODENAME" "$DEB_FILE"

echo "✓ 패키지 추가 완료"
echo ""
echo "클라이언트에서 업데이트:"
echo "  sudo apt update"
EOF
    chmod +x /usr/local/bin/apt-repo-add
    
    # apt-repo-remove: 패키지 제거
    cat > /usr/local/bin/apt-repo-remove << 'EOF'
#!/bin/bash
# APT 저장소에서 패키지 제거
# 사용법: apt-repo-remove <package-name> [codename]

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

echo "✓ 패키지 제거 완료: $PACKAGE"
EOF
    chmod +x /usr/local/bin/apt-repo-remove
    
    # apt-repo-list: 패키지 목록
    cat > /usr/local/bin/apt-repo-list << 'EOF'
#!/bin/bash
# APT 저장소 패키지 목록
# 사용법: apt-repo-list [codename]

REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

CODENAME="${1:-stable}"

cd "$REPO_DIR"
reprepro list "$CODENAME"
EOF
    chmod +x /usr/local/bin/apt-repo-list
    
    # apt-repo-info: 저장소 정보
    cat > /usr/local/bin/apt-repo-info << 'EOF'
#!/bin/bash
# APT 저장소 정보

REPO_DIR="/var/www/apt/repo"

echo "═══════════════════════════════════════════════════════════"
echo "  APT Repository Information"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "저장소 경로: $REPO_DIR"
echo ""
echo "배포판:"
cat "$REPO_DIR/conf/distributions"
echo ""
echo "등록된 패키지:"
apt-repo-list 2>/dev/null || echo "  (없음)"
echo ""
echo "디스크 사용량:"
du -sh "$REPO_DIR/pool" 2>/dev/null || echo "  0"
EOF
    chmod +x /usr/local/bin/apt-repo-info
    
    echo -e "${GREEN}✓${NC} 관리 스크립트 생성 완료"
    echo ""
    echo "사용 가능한 명령어:"
    echo "  apt-repo-add <package.deb>    # 패키지 추가"
    echo "  apt-repo-remove <name>        # 패키지 제거"
    echo "  apt-repo-list                 # 패키지 목록"
    echo "  apt-repo-info                 # 저장소 정보"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 클라이언트 설정 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_client_script() {
    print_header "클라이언트 설정 스크립트 생성"
    
    cat > "$REPO_DIR/setup-client.sh" << 'CLIENTEOF'
#!/bin/bash
#===============================================================================
# APT 저장소 클라이언트 설정
# 
# 사용법:
#   curl -fsSL https://DOMAIN/setup-client.sh | sudo bash
#   # 또는 인증이 필요한 경우
#   curl -fsSL -u USER:PASS https://DOMAIN/setup-client.sh | sudo bash -s -- USER PASS
#===============================================================================

set -e

DOMAIN="DOMAIN_PLACEHOLDER"
AUTH_USER="${1:-}"
AUTH_PASS="${2:-}"
CODENAME="CODENAME_PLACEHOLDER"

echo "APT 저장소 클라이언트 설정 중..."

# GPG 키 추가
echo "1. GPG 키 추가..."
if [[ -n "$AUTH_USER" ]]; then
    curl -fsSL -u "$AUTH_USER:$AUTH_PASS" "https://$DOMAIN/KEY.gpg" | \
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
else
    curl -fsSL "https://$DOMAIN/KEY.gpg" | \
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
fi

# 인증 설정 (필요한 경우)
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

# sources.list 추가
echo "3. APT 소스 추가..."
cat > /etc/apt/sources.list.d/internal.list << EOF
deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://$DOMAIN $CODENAME main
EOF

# 업데이트
echo "4. 패키지 목록 업데이트..."
apt-get update

echo ""
echo "✓ 설정 완료!"
echo ""
echo "사용 예:"
echo "  sudo apt install vaultctl"
echo "  sudo apt update && sudo apt upgrade"
CLIENTEOF

    # placeholder 교체
    sed -i "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$REPO_DIR/setup-client.sh"
    sed -i "s/CODENAME_PLACEHOLDER/$REPO_CODENAME/g" "$REPO_DIR/setup-client.sh"
    chmod +x "$REPO_DIR/setup-client.sh"
    
    echo -e "${GREEN}✓${NC} 클라이언트 스크립트 생성: $REPO_DIR/setup-client.sh"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 랜딩 페이지 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_landing_page() {
    cat > "$REPO_DIR/index.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Internal APT Repository</title>
    <style>
        :root { --bg: #1a1a2e; --fg: #eaeaea; --accent: #00d9ff; --code-bg: #16213e; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
               background: var(--bg); color: var(--fg); max-width: 800px; margin: 0 auto; padding: 40px 20px; }
        h1 { color: var(--accent); }
        h2 { border-bottom: 1px solid #333; padding-bottom: 8px; margin-top: 32px; }
        pre { background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; 
              border: 1px solid #333; }
        code { font-family: 'SF Mono', Monaco, 'Consolas', monospace; font-size: 14px; }
        a { color: var(--accent); }
        .badge { display: inline-block; background: #00d97e; color: #1a1a2e; padding: 4px 12px; 
                 border-radius: 4px; font-size: 12px; font-weight: 600; margin-left: 8px; }
        .warning { background: #3d2914; border-left: 4px solid #ff9500; padding: 12px 16px; 
                   border-radius: 0 8px 8px 0; margin: 16px 0; }
    </style>
</head>
<body>
    <h1>🔐 Internal APT Repository <span class="badge">Private</span></h1>
    <p>내부 패키지 배포를 위한 APT 저장소입니다.</p>
    
    <h2>🚀 Quick Setup</h2>
    <pre><code>curl -fsSL https://DOMAIN/setup-client.sh | sudo bash -s -- USERNAME PASSWORD</code></pre>
    
    <div class="warning">
        <strong>⚠️ 인증 필요</strong><br>
        이 저장소는 인증이 필요합니다. 관리자에게 자격 증명을 요청하세요.
    </div>
    
    <h2>📦 Manual Setup</h2>
    <pre><code># 1. GPG 키 추가
curl -fsSL -u USER:PASS https://DOMAIN/KEY.gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

# 2. 인증 설정
echo "machine DOMAIN login USER password PASS" | \
    sudo tee /etc/apt/auth.conf.d/internal.conf
sudo chmod 600 /etc/apt/auth.conf.d/internal.conf

# 3. 저장소 추가
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://DOMAIN stable main" | \
    sudo tee /etc/apt/sources.list.d/internal.list

# 4. 설치
sudo apt update
sudo apt install vaultctl</code></pre>
    
    <h2>📋 Available Packages</h2>
    <p>등록된 패키지 목록은 인증 후 확인할 수 있습니다.</p>
    <pre><code>apt-cache search --names-only '.*' 2>/dev/null | grep -v "^lib"</code></pre>
    
    <h2>🔗 Files</h2>
    <ul>
        <li><a href="/KEY.gpg">GPG Public Key (ASCII)</a></li>
        <li><a href="/setup-client.sh">Client Setup Script</a></li>
    </ul>
</body>
</html>
HTMLEOF

    sed -i "s/DOMAIN/$DOMAIN/g" "$REPO_DIR/index.html"
    
    echo -e "${GREEN}✓${NC} 랜딩 페이지 생성"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 최종 안내
# ═══════════════════════════════════════════════════════════════════════════════

print_summary() {
    print_header "설치 완료!"
    
    echo "APT 저장소가 성공적으로 구축되었습니다."
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  저장소 정보"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  URL:        https://$DOMAIN"
    echo "  저장소:     $REPO_DIR"
    echo "  GPG Home:   $GPG_HOME"
    echo ""
    
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  인증 정보 (안전하게 보관하세요!)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "  사용자:     $AUTH_USER"
        echo "  비밀번호:   $AUTH_PASS"
        echo ""
        echo "  저장된 위치: $REPO_BASE/.credentials"
        echo ""
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  사용 방법"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  패키지 추가:"
    echo "    apt-repo-add vaultctl_0.1.0_amd64.deb"
    echo ""
    echo "  패키지 목록:"
    echo "    apt-repo-list"
    echo ""
    echo "  클라이언트 설정:"
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        echo "    curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash -s -- $AUTH_USER $AUTH_PASS"
    else
        echo "    curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════════════

main() {
    # root 확인
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}✗ root 권한이 필요합니다.${NC}"
        exit 1
    fi
    
    print_header "개인 APT 저장소 구축"
    
    install_packages
    setup_directories
    setup_gpg
    setup_reprepro
    setup_auth
    setup_caddy
    create_management_scripts
    create_client_script
    create_landing_page
    print_summary
}

main "$@"
