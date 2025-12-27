#!/bin/bash
#===============================================================================
# 개인 APT 저장소 서버 구축 스크립트 (Caddy 독립 실행)
# 
# 특징:
#   - 대화형 설정 입력
#   - 재설치 시 기존 값 표시 및 업데이트 지원
#   - reprepro + GPG 서명
#   - Caddy 웹서버 (자동 HTTPS)
#
# 사용법:
#   sudo ./setup-apt-server.sh
#   sudo ./setup-apt-server.sh --reconfigure  # 설정만 변경
#===============================================================================

set -e

# ═══════════════════════════════════════════════════════════════════════════════
# 상수 및 색상
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_FILE="/var/www/apt/.config"
REPO_BASE="/var/www/apt"
REPO_DIR="$REPO_BASE/repo"
GPG_HOME="$REPO_BASE/.gnupg"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════════
# 유틸리티 함수
# ═══════════════════════════════════════════════════════════════════════════════

print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_step() {
    echo -e "${CYAN}▶${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}!${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# 사용자 입력 받기 (기본값 지원)
prompt_input() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    local is_password="${4:-false}"
    local input
    
    if [[ -n "$default" ]]; then
        if [[ "$is_password" == "true" ]]; then
            echo -en "${BOLD}$prompt${NC} [********]: "
        else
            echo -en "${BOLD}$prompt${NC} [${CYAN}$default${NC}]: "
        fi
    else
        echo -en "${BOLD}$prompt${NC}: "
    fi
    
    if [[ "$is_password" == "true" ]]; then
        read -s input
        echo ""
    else
        read input
    fi
    
    if [[ -z "$input" ]]; then
        eval "$var_name=\"$default\""
    else
        eval "$var_name=\"$input\""
    fi
}

# Yes/No 프롬프트
prompt_yn() {
    local prompt="$1"
    local default="$2"
    local answer
    
    if [[ "$default" == "y" ]]; then
        echo -en "${BOLD}$prompt${NC} [Y/n]: "
    else
        echo -en "${BOLD}$prompt${NC} [y/N]: "
    fi
    
    read answer
    answer=${answer:-$default}
    
    [[ "$answer" =~ ^[Yy] ]]
}

# ═══════════════════════════════════════════════════════════════════════════════
# 설정 로드/저장
# ═══════════════════════════════════════════════════════════════════════════════

load_existing_config() {
    if [[ -f "$CONFIG_FILE" ]]; then
        source "$CONFIG_FILE"
        return 0
    fi
    return 1
}

save_config() {
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << EOF
# APT 저장소 설정 (자동 생성됨)
# 생성일: $(date '+%Y-%m-%d %H:%M:%S')
# 웹서버: Caddy (자동 HTTPS)

DOMAIN="$DOMAIN"
GPG_EMAIL="$GPG_EMAIL"
GPG_NAME="$GPG_NAME"
REPO_NAME="$REPO_NAME"
REPO_LABEL="$REPO_LABEL"
REPO_CODENAME="$REPO_CODENAME"
REPO_ARCH="$REPO_ARCH"
ENABLE_AUTH="$ENABLE_AUTH"
AUTH_USER="$AUTH_USER"
AUTH_PASS="$AUTH_PASS"
WEB_SERVER="caddy"
EOF
    chmod 600 "$CONFIG_FILE"
    print_success "설정 저장됨: $CONFIG_FILE"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 대화형 설정
# ═══════════════════════════════════════════════════════════════════════════════

interactive_config() {
    print_header "APT 저장소 설정"
    
    # 기존 설정 로드
    local existing_config=false
    if load_existing_config; then
        existing_config=true
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${YELLOW}  기존 설정 발견${NC}"
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "  도메인:       $DOMAIN"
        echo "  GPG 이메일:   $GPG_EMAIL"
        echo "  저장소 이름:  $REPO_NAME"
        echo "  코드네임:     $REPO_CODENAME"
        echo "  인증 사용:    $ENABLE_AUTH"
        if [[ "$ENABLE_AUTH" == "true" ]]; then
            echo "  인증 사용자:  $AUTH_USER"
        fi
        echo ""
    fi
    
    # 기본값 설정 (기존 값 또는 샘플)
    local default_domain="${DOMAIN:-}"
    local default_gpg_email="${GPG_EMAIL:-}"
    local default_gpg_name="${GPG_NAME:-APT Repository Signing Key}"
    local default_repo_name="${REPO_NAME:-internal}"
    local default_repo_label="${REPO_LABEL:-Internal Repository}"
    local default_codename="${REPO_CODENAME:-stable}"
    local default_arch="${REPO_ARCH:-amd64}"
    local default_auth_user="${AUTH_USER:-apt}"
    local default_enable_auth="${ENABLE_AUTH:-true}"
    
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  기본 설정 (필수)${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${YELLOW}참고: Caddy가 자동으로 Let's Encrypt 인증서를 발급합니다.${NC}"
    echo -e "${YELLOW}      도메인의 DNS가 이 서버를 가리켜야 합니다.${NC}"
    echo ""
    
    # 필수 입력 - 도메인
    while [[ -z "$DOMAIN" ]]; do
        prompt_input "도메인 (예: apt.example.com)" "$default_domain" "DOMAIN"
        if [[ -z "$DOMAIN" ]]; then
            print_error "도메인은 필수입니다."
        fi
    done
    
    # 필수 입력 - GPG 이메일
    while [[ -z "$GPG_EMAIL" ]]; do
        prompt_input "GPG 서명용 이메일 (예: apt@example.com)" "$default_gpg_email" "GPG_EMAIL"
        if [[ -z "$GPG_EMAIL" ]]; then
            print_error "GPG 이메일은 필수입니다."
        fi
    done
    
    prompt_input "GPG 키 이름" "$default_gpg_name" "GPG_NAME"
    
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  저장소 설정${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    prompt_input "저장소 이름 (Origin)" "$default_repo_name" "REPO_NAME"
    prompt_input "저장소 라벨" "$default_repo_label" "REPO_LABEL"
    prompt_input "배포판 코드네임" "$default_codename" "REPO_CODENAME"
    prompt_input "아키텍처" "$default_arch" "REPO_ARCH"
    
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  인증 설정${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    local auth_default="y"
    [[ "$default_enable_auth" == "false" ]] && auth_default="n"
    
    if prompt_yn "Basic Auth 인증을 사용하시겠습니까?" "$auth_default"; then
        ENABLE_AUTH="true"
        prompt_input "인증 사용자명" "$default_auth_user" "AUTH_USER"
        
        echo -en "${BOLD}인증 비밀번호${NC} [Enter=자동생성/기존유지]: "
        read -s input_pass
        echo ""
        
        if [[ -z "$input_pass" ]]; then
            if [[ -z "$AUTH_PASS" ]]; then
                AUTH_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
                echo -e "  ${GREEN}비밀번호 자동 생성됨${NC}"
            else
                echo -e "  ${GREEN}기존 비밀번호 유지${NC}"
            fi
        else
            AUTH_PASS="$input_pass"
            echo -e "  ${GREEN}새 비밀번호 설정됨${NC}"
        fi
    else
        ENABLE_AUTH="false"
        AUTH_USER=""
        AUTH_PASS=""
    fi
    
    # 설정 확인
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  설정 확인${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  도메인:        $DOMAIN"
    echo "  GPG 이메일:    $GPG_EMAIL"
    echo "  GPG 키 이름:   $GPG_NAME"
    echo "  저장소 이름:   $REPO_NAME"
    echo "  코드네임:      $REPO_CODENAME"
    echo "  웹서버:        Caddy (자동 HTTPS)"
    echo "  인증 사용:     $ENABLE_AUTH"
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        echo "  인증 사용자:   $AUTH_USER"
        echo "  인증 비밀번호: ********"
    fi
    echo ""
    
    if ! prompt_yn "이 설정으로 진행하시겠습니까?" "y"; then
        echo ""
        print_warning "설정이 취소되었습니다. 다시 실행해주세요."
        exit 0
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# 패키지 설치
# ═══════════════════════════════════════════════════════════════════════════════

install_packages() {
    print_header "패키지 설치"
    
    print_step "apt 업데이트..."
    apt-get update -qq
    
    print_step "필수 패키지 설치..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        reprepro \
        gnupg \
        gnupg-agent \
        apache2-utils \
        curl \
        rng-tools 2>/dev/null || true
    
    # Caddy 설치
    if ! command -v caddy &> /dev/null; then
        print_step "Caddy 웹서버 설치..."
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
            gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
            tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
    else
        print_success "Caddy 이미 설치됨"
    fi
    
    print_success "패키지 설치 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 디렉토리 구조 생성
# ═══════════════════════════════════════════════════════════════════════════════

setup_directories() {
    print_header "디렉토리 구조"
    
    print_step "디렉토리 생성..."
    mkdir -p "$REPO_DIR"/{conf,db,dists,pool,incoming}
    mkdir -p "$GPG_HOME"
    
    chmod 700 "$GPG_HOME"
    chown -R www-data:www-data "$REPO_BASE"
    
    print_success "디렉토리 생성 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# GPG 키 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_gpg() {
    print_header "GPG 서명 키 설정"
    
    export GNUPGHOME="$GPG_HOME"
    
    # 기존 키 확인
    local need_new_key=false
    
    if gpg --list-keys 2>/dev/null | grep -q "$GPG_EMAIL"; then
        print_success "기존 GPG 키 발견: $GPG_EMAIL"
    elif gpg --list-keys 2>/dev/null | grep -q uid; then
        # 다른 이메일의 키가 있음
        local current_email
        current_email=$(gpg --list-keys --with-colons 2>/dev/null | grep uid | head -1 | cut -d: -f10 | grep -oP '<\K[^>]+' || echo "unknown")
        print_warning "다른 GPG 키 발견: $current_email"
        
        if prompt_yn "새 GPG 키를 생성하시겠습니까? (기존 키 삭제됨)" "y"; then
            rm -rf "$GPG_HOME"/*
            need_new_key=true
        fi
    else
        need_new_key=true
    fi
    
    if [[ "$need_new_key" == "true" ]]; then
        create_gpg_key
    fi
    
    # Key ID 추출
    GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT 2>/dev/null | grep -E '^\s+[A-F0-9]+' | awk '{print $1}' | head -1)
    
    if [[ -z "$GPG_KEY_ID" ]]; then
        GPG_KEY_ID=$(gpg --list-keys --keyid-format LONG 2>/dev/null | grep -oP '[A-F0-9]{16}' | head -1)
        GPG_KEY_ID="${GPG_KEY_ID: -8}"
    fi
    
    echo "  Key ID: $GPG_KEY_ID"
    
    # 공개키 내보내기
    print_step "공개키 내보내기..."
    gpg --armor --export > "$REPO_DIR/KEY.gpg"
    gpg --export > "$REPO_DIR/KEY"
    
    export GPG_KEY_ID
    
    print_success "GPG 설정 완료"
}

create_gpg_key() {
    print_step "새 GPG 키 생성 중... (시간이 걸릴 수 있습니다)"
    
    cat > /tmp/gpg-batch << EOF
%echo Generating APT signing key
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: $GPG_NAME
Name-Email: $GPG_EMAIL
Expire-Date: 0
%no-protection
%commit
%echo Done
EOF
    
    gpg --batch --gen-key /tmp/gpg-batch 2>/dev/null
    rm -f /tmp/gpg-batch
    
    print_success "GPG 키 생성 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# reprepro 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_reprepro() {
    print_header "reprepro 설정"
    
    export GNUPGHOME="$GPG_HOME"
    
    print_step "distributions 설정 업데이트..."
    cat > "$REPO_DIR/conf/distributions" << EOF
Origin: $REPO_NAME
Label: $REPO_LABEL
Codename: $REPO_CODENAME
Architectures: $REPO_ARCH
Components: main
Description: $REPO_LABEL
SignWith: $GPG_KEY_ID
EOF

    print_step "options 설정 업데이트..."
    cat > "$REPO_DIR/conf/options" << EOF
verbose
basedir $REPO_DIR
gnupghome $GPG_HOME
ask-passphrase
EOF

    print_step "저장소 초기화..."
    cd "$REPO_DIR"
    reprepro export 2>/dev/null || true
    
    print_success "reprepro 설정 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 인증 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_auth() {
    print_header "인증 설정"
    
    if [[ "$ENABLE_AUTH" != "true" ]]; then
        print_warning "인증 비활성화 (공개 저장소)"
        rm -f "$REPO_BASE/.htpasswd"
        rm -f "$REPO_BASE/.credentials"
        return
    fi
    
    print_step "htpasswd 파일 생성/업데이트..."
    htpasswd -bc "$REPO_BASE/.htpasswd" "$AUTH_USER" "$AUTH_PASS"
    chmod 600 "$REPO_BASE/.htpasswd"
    chown www-data:www-data "$REPO_BASE/.htpasswd"
    
    # Caddy용 해시 생성
    print_step "Caddy 인증 해시 생성..."
    CADDY_HASH=$(caddy hash-password --plaintext "$AUTH_PASS" 2>/dev/null || echo "")
    
    print_step "인증 정보 저장..."
    cat > "$REPO_BASE/.credentials" << EOF
# APT 저장소 인증 정보
# 생성일: $(date '+%Y-%m-%d %H:%M:%S')
USER=$AUTH_USER
PASS=$AUTH_PASS
URL=https://$DOMAIN
EOF
    chmod 600 "$REPO_BASE/.credentials"
    
    print_success "인증 설정 완료"
    
    echo ""
    echo -e "${YELLOW}┌─────────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│  인증 정보 (안전하게 보관하세요!)                       │${NC}"
    echo -e "${YELLOW}├─────────────────────────────────────────────────────────┤${NC}"
    printf "${YELLOW}│  사용자:   %-44s│${NC}\n" "$AUTH_USER"
    printf "${YELLOW}│  비밀번호: %-44s│${NC}\n" "$AUTH_PASS"
    echo -e "${YELLOW}└─────────────────────────────────────────────────────────┘${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Caddy 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_caddy() {
    print_header "Caddy 웹서버 설정"
    
    print_step "Caddy 설정 업데이트..."
    
    # Caddy 해시 (이미 생성되어 있거나 새로 생성)
    if [[ -z "$CADDY_HASH" ]] && [[ "$ENABLE_AUTH" == "true" ]]; then
        CADDY_HASH=$(caddy hash-password --plaintext "$AUTH_PASS" 2>/dev/null || echo "")
    fi
    
    if [[ "$ENABLE_AUTH" == "true" ]] && [[ -n "$CADDY_HASH" ]]; then
        cat > /etc/caddy/Caddyfile << EOF
$DOMAIN {
    root * $REPO_DIR
    
    # 공개 파일 (GPG 키, 설치 스크립트)
    @public {
        path /KEY.gpg /KEY /add-key.sh /setup-client.sh /index.html
    }
    handle @public {
        file_server
    }
    
    # 나머지는 인증 필요
    handle {
        basicauth {
            $AUTH_USER $CADDY_HASH
        }
        file_server browse
    }
    
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
    
    # 로그 디렉토리 생성
    mkdir -p /var/log/caddy
    chown caddy:caddy /var/log/caddy
    
    print_step "Caddy 테스트 및 재시작..."
    caddy validate --config /etc/caddy/Caddyfile 2>/dev/null || {
        print_error "Caddy 설정 오류"
        cat /etc/caddy/Caddyfile
        exit 1
    }
    
    systemctl enable caddy
    systemctl restart caddy
    
    print_success "Caddy 설정 완료 (자동 HTTPS)"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 관리 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_management_scripts() {
    print_header "관리 스크립트"
    
    print_step "apt-repo-add 생성..."
    cat > /usr/local/bin/apt-repo-add << 'EOF'
#!/bin/bash
set -e
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

source /var/www/apt/.config 2>/dev/null || true
CODENAME="${REPO_CODENAME:-stable}"

DEB_FILE="$1"
TARGET_CODENAME="${2:-$CODENAME}"

if [[ -z "$DEB_FILE" ]] || [[ ! -f "$DEB_FILE" ]]; then
    echo "사용법: apt-repo-add <package.deb> [codename]"
    echo "현재 코드네임: $CODENAME"
    exit 1
fi

echo "패키지 추가: $DEB_FILE → $TARGET_CODENAME"
dpkg-deb --info "$DEB_FILE" | grep -E '^ (Package|Version|Architecture):'

cd "$REPO_DIR"
reprepro includedeb "$TARGET_CODENAME" "$DEB_FILE"

echo "✓ 완료"
EOF
    chmod +x /usr/local/bin/apt-repo-add
    
    print_step "apt-repo-remove 생성..."
    cat > /usr/local/bin/apt-repo-remove << 'EOF'
#!/bin/bash
set -e
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

source /var/www/apt/.config 2>/dev/null || true
CODENAME="${REPO_CODENAME:-stable}"

PACKAGE="$1"
TARGET_CODENAME="${2:-$CODENAME}"

if [[ -z "$PACKAGE" ]]; then
    echo "사용법: apt-repo-remove <package-name> [codename]"
    exit 1
fi

cd "$REPO_DIR"
reprepro remove "$TARGET_CODENAME" "$PACKAGE"
echo "✓ 제거 완료: $PACKAGE"
EOF
    chmod +x /usr/local/bin/apt-repo-remove
    
    print_step "apt-repo-list 생성..."
    cat > /usr/local/bin/apt-repo-list << 'EOF'
#!/bin/bash
REPO_DIR="/var/www/apt/repo"
GNUPGHOME="/var/www/apt/.gnupg"
export GNUPGHOME

source /var/www/apt/.config 2>/dev/null || true
CODENAME="${REPO_CODENAME:-stable}"

cd "$REPO_DIR"
reprepro list "${1:-$CODENAME}"
EOF
    chmod +x /usr/local/bin/apt-repo-list
    
    print_step "apt-repo-info 생성..."
    cat > /usr/local/bin/apt-repo-info << 'EOF'
#!/bin/bash
source /var/www/apt/.config 2>/dev/null || {
    echo "설정 파일이 없습니다: /var/www/apt/.config"
    exit 1
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  APT 저장소 정보"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  URL:        https://$DOMAIN"
echo "  저장소:     /var/www/apt/repo"
echo "  코드네임:   $REPO_CODENAME"
echo "  웹서버:     Caddy (자동 HTTPS)"
echo ""
if [[ "$ENABLE_AUTH" == "true" ]]; then
    echo "  인증:       활성화"
    echo "  사용자:     $AUTH_USER"
    echo "  비밀번호:   $AUTH_PASS"
    echo ""
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  패키지 목록"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
apt-repo-list
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  클라이언트 설정 명령어"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$ENABLE_AUTH" == "true" ]]; then
    echo ""
    echo "  curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash -s -- $AUTH_USER '$AUTH_PASS'"
else
    echo ""
    echo "  curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash"
fi
echo ""
EOF
    chmod +x /usr/local/bin/apt-repo-info
    
    print_success "관리 스크립트 생성 완료"
    echo "  apt-repo-add / apt-repo-remove / apt-repo-list / apt-repo-info"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 클라이언트 스크립트 생성
# ═══════════════════════════════════════════════════════════════════════════════

create_client_scripts() {
    print_header "클라이언트 스크립트"
    
    print_step "setup-client.sh 생성..."
    cat > "$REPO_DIR/setup-client.sh" << CLIENTEOF
#!/bin/bash
set -e

DOMAIN="$DOMAIN"
AUTH_USER="\${1:-}"
AUTH_PASS="\${2:-}"
CODENAME="$REPO_CODENAME"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  APT 저장소 클라이언트 설정"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  도메인:   \$DOMAIN"
echo "  코드네임: \$CODENAME"
echo ""

# 1. GPG 키 추가
echo "1. GPG 키 추가..."
rm -f /usr/share/keyrings/internal-apt.gpg
if [[ -n "\$AUTH_USER" ]]; then
    curl -fsSL -u "\$AUTH_USER:\$AUTH_PASS" "https://\$DOMAIN/KEY.gpg" | \\
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
else
    curl -fsSL "https://\$DOMAIN/KEY.gpg" | \\
        gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
fi
echo "   ✓ GPG 키 설치됨"

# 2. 인증 설정
if [[ -n "\$AUTH_USER" ]]; then
    echo "2. 인증 설정..."
    mkdir -p /etc/apt/auth.conf.d
    cat > /etc/apt/auth.conf.d/internal.conf << AUTHEOF
machine \$DOMAIN
login \$AUTH_USER
password \$AUTH_PASS
AUTHEOF
    chmod 600 /etc/apt/auth.conf.d/internal.conf
    echo "   ✓ 인증 설정됨"
fi

# 3. APT 소스 추가
echo "3. APT 소스 추가..."
cat > /etc/apt/sources.list.d/internal.list << SRCEOF
deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://\$DOMAIN \$CODENAME main
SRCEOF
echo "   ✓ APT 소스 추가됨"

# 4. 업데이트
echo "4. APT 업데이트..."
apt-get update -qq

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ 설정 완료!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  설치 예시:"
echo "    sudo apt install vaultctl"
echo ""
CLIENTEOF
    chmod +x "$REPO_DIR/setup-client.sh"
    
    print_step "index.html 생성..."
    cat > "$REPO_DIR/index.html" << EOF
<!DOCTYPE html>
<html>
<head>
    <title>APT Repository - $DOMAIN</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }
        h2 { color: #555; margin-top: 30px; }
        a { color: #0066cc; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 14px; }
        pre { background: #2d2d2d; color: #f8f8f2; padding: 15px; border-radius: 5px; overflow-x: auto; }
        .info { background: #e7f3ff; border-left: 4px solid #0066cc; padding: 10px 15px; margin: 20px 0; }
        .secure { background: #e8f5e9; border-left: 4px solid #4caf50; padding: 10px 15px; margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🗄️ Internal APT Repository</h1>
        
        <p><a href="/KEY.gpg">📜 GPG Key</a> | <a href="/setup-client.sh">📥 Setup Script</a></p>
        
        <div class="info">
            <strong>도메인:</strong> $DOMAIN<br>
            <strong>코드네임:</strong> $REPO_CODENAME
        </div>
        
        <div class="secure">
            🔒 <strong>HTTPS 자동 활성화</strong> - Caddy + Let's Encrypt
        </div>
        
        <h2>Quick Setup</h2>
        <pre>curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash -s -- USER PASSWORD</pre>
        
        <h2>Manual Setup</h2>
        <pre>
# 1. GPG 키 추가
curl -fsSL -u USER:PASS https://$DOMAIN/KEY.gpg | \\
    sudo gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

# 2. 인증 설정
echo "machine $DOMAIN login USER password PASS" | \\
    sudo tee /etc/apt/auth.conf.d/internal.conf
sudo chmod 600 /etc/apt/auth.conf.d/internal.conf

# 3. APT 소스 추가
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://$DOMAIN $REPO_CODENAME main" | \\
    sudo tee /etc/apt/sources.list.d/internal.list

# 4. 설치
sudo apt update
sudo apt install vaultctl
        </pre>
    </div>
</body>
</html>
EOF
    
    # 파일 권한 설정
    chown -R www-data:www-data "$REPO_DIR"
    
    print_success "클라이언트 스크립트 생성 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 최종 안내
# ═══════════════════════════════════════════════════════════════════════════════

print_summary() {
    print_header "설치 완료"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  저장소 정보"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  URL:        https://$DOMAIN"
    echo "  저장소:     $REPO_DIR"
    echo "  코드네임:   $REPO_CODENAME"
    echo "  웹서버:     Caddy (자동 HTTPS)"
    echo ""
    
    if [[ "$ENABLE_AUTH" == "true" ]]; then
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
    echo "  1. DNS 설정 확인:"
    echo "     $DOMAIN → 이 서버 IP"
    echo ""
    echo "  2. 패키지 추가:"
    echo "     apt-repo-add vaultctl_0.1.0_amd64.deb"
    echo ""
    echo "  3. 클라이언트 설정:"
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        echo "     curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash -s -- $AUTH_USER '$AUTH_PASS'"
    else
        echo "     curl -fsSL https://$DOMAIN/setup-client.sh | sudo bash"
    fi
    echo ""
    echo "  4. 저장소 정보 확인:"
    echo "     apt-repo-info"
    echo ""
    echo "  5. HTTPS 인증서 확인 (몇 분 후):"
    echo "     curl -I https://$DOMAIN"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════════════

main() {
    if [[ $EUID -ne 0 ]]; then
        print_error "root 권한이 필요합니다."
        echo "  sudo $0"
        exit 1
    fi
    
    echo ""
    echo -e "${BOLD}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║     APT 저장소 서버 설치 (Caddy 자동 HTTPS)               ║${NC}"
    echo -e "${BOLD}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # 재설정 모드 확인
    local reconfigure_only=false
    if [[ "$1" == "--reconfigure" ]] || [[ "$1" == "-r" ]]; then
        reconfigure_only=true
        if [[ ! -d "$REPO_DIR" ]]; then
            print_error "APT 저장소가 설치되지 않았습니다. 전체 설치를 진행하세요."
            exit 1
        fi
        echo -e "${YELLOW}재설정 모드: 설정만 업데이트합니다.${NC}"
        echo ""
    fi
    
    # 대화형 설정
    interactive_config
    
    if [[ "$reconfigure_only" == "true" ]]; then
        save_config
        setup_reprepro
        setup_auth
        setup_caddy
        create_client_scripts
        print_summary
    else
        install_packages
        setup_directories
        setup_gpg
        save_config
        setup_reprepro
        setup_auth
        setup_caddy
        create_management_scripts
        create_client_scripts
        print_summary
    fi
}

main "$@"
