#!/bin/bash
#===============================================================================
# 개인 APT 저장소 서버 구축 스크립트 (Traefik 환경용)
# 
# 특징:
#   - 대화형 설정 입력
#   - 재설치 시 기존 값 표시 및 업데이트 지원
#   - reprepro + GPG 서명
#   - Nginx (Traefik 백엔드)
#
# 사용법:
#   sudo ./setup-apt-server-traefik.sh
#   sudo ./setup-apt-server-traefik.sh --reconfigure  # 설정만 변경
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
LISTEN_PORT="$LISTEN_PORT"
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
        echo "  리스닝 포트:  $LISTEN_PORT"
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
    local default_port="${LISTEN_PORT:-80}"
    local default_enable_auth="${ENABLE_AUTH:-true}"
    
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  기본 설정 (필수)${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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
    
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  네트워크 설정${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    prompt_input "Nginx 리스닝 포트 (Traefik 백엔드)" "$default_port" "LISTEN_PORT"
    
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
    echo "  인증 사용:     $ENABLE_AUTH"
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        echo "  인증 사용자:   $AUTH_USER"
        echo "  인증 비밀번호: ********"
    fi
    echo "  리스닝 포트:   $LISTEN_PORT"
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
        nginx \
        apache2-utils \
        curl \
        rng-tools 2>/dev/null || true
    
    print_success "패키지 설치 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 디렉토리 구조 생성
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# Fancyindex 테마 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_fancyindex_theme() {
    print_step "Fancyindex 테마 설치..."
    
    mkdir -p "$REPO_DIR/.theme"
    
    # 템플릿 파일 찾기
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    THEME_DIR=""
    
    for path in "$SCRIPT_DIR/../templates/fancyindex" "$SCRIPT_DIR/templates/fancyindex" "/opt/vaultctl/templates/fancyindex"; do
        resolved_path="$(realpath "$path" 2>/dev/null || echo "")"
        if [[ -n "$resolved_path" ]] && [[ -d "$resolved_path" ]]; then
            THEME_DIR="$resolved_path"
            break
        fi
    done
    
    if [[ -n "$THEME_DIR" ]] && [[ -f "$THEME_DIR/header.html" ]]; then
        cp "$THEME_DIR/header.html" "$REPO_DIR/.theme/"
        cp "$THEME_DIR/footer.html" "$REPO_DIR/.theme/"
        print_step "테마 사용: $THEME_DIR"
    else
        # 템플릿이 없으면 인라인으로 생성
        cat > "$REPO_DIR/.theme/header.html" << 'HEADEREOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APT Repository</title>
    <style>
        :root {
            --background: #ffffff;
            --foreground: #0a0a0a;
            --muted: #f4f4f5;
            --muted-foreground: #71717a;
            --border: #e4e4e7;
            --radius: 0.5rem;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--background);
            color: var(--foreground);
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 48px 24px;
        }
        .header {
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .header h1 {
            font-size: 24px;
            font-weight: 600;
            letter-spacing: -0.025em;
        }
        .header h1 a {
            color: var(--foreground);
            text-decoration: none;
        }
        .header h1 a:hover {
            text-decoration: underline;
        }
        #list {
            background: var(--background);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            overflow: hidden;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        thead {
            background: var(--muted);
        }
        th {
            text-align: left;
            padding: 12px 16px;
            font-weight: 500;
            color: var(--muted-foreground);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--border);
        }
        th a {
            color: var(--muted-foreground);
            text-decoration: none;
        }
        td {
            padding: 10px 16px;
            border-bottom: 1px solid var(--border);
        }
        tr:last-child td {
            border-bottom: none;
        }
        tbody tr:hover {
            background: var(--muted);
        }
        td a, .list a {
            color: var(--foreground);
            text-decoration: none;
            font-weight: 500;
        }
        td a:hover, .list a:hover {
            text-decoration: underline;
        }
        .size, .date {
            color: var(--muted-foreground);
            font-family: ui-monospace, 'SF Mono', 'Consolas', monospace;
            font-size: 13px;
        }
        .footer {
            margin-top: 32px;
            padding-top: 24px;
            border-top: 1px solid var(--border);
            text-align: center;
            font-size: 12px;
            color: var(--muted-foreground);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><a href="/">APT Repository</a></h1>
        </div>
        <div id="list">
HEADEREOF
        
        cat > "$REPO_DIR/.theme/footer.html" << 'FOOTEREOF'
        </div>
        <div class="footer">
            Powered by reprepro + GPG signing
        </div>
    </div>
</body>
</html>
FOOTEREOF
    fi
    
    chown -R www-data:www-data "$REPO_DIR/.theme" 2>/dev/null || true
}

# ═══════════════════════════════════════════════════════════════════════════════
# 디렉토리 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_directories() {
    print_header "디렉토리 구조"
    
    print_step "디렉토리 생성..."
    mkdir -p "$REPO_DIR"/{conf,db,dists,pool}
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
    
    echo -e "${YELLOW}참고: 이 GPG 키는 패키지 서명용입니다.${NC}"
    echo -e "${YELLOW}      HTTPS 인증서(Traefik/Let's Encrypt)와는 별개입니다.${NC}"
    echo ""
    
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
    gpg --armor --export > "$REPO_DIR/key.gpg"
    gpg --export > "$REPO_DIR/key"
    
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
# Nginx 설정
# ═══════════════════════════════════════════════════════════════════════════════

setup_nginx() {
    print_header "Nginx 설정"
    
    # fancyindex 모듈 설치
    print_step "fancyindex 모듈 설치..."
    apt-get install -y libnginx-mod-http-fancyindex >/dev/null 2>&1 || true
    
    print_step "Nginx 설정 업데이트..."
    rm -f /etc/nginx/sites-enabled/default
    
    # fancyindex 테마 설치
    setup_fancyindex_theme
    
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        # Private: 인증 필요, autoindex off (보안)
        cat > /etc/nginx/sites-available/apt-repo << EOF
server {
    listen $LISTEN_PORT;
    server_name $DOMAIN;
    
    root $REPO_DIR;
    
    # Public files (GPG key, setup script)
    location ~ ^/(key\.gpg|key|setup-client\.sh|index\.html)$ {
        allow all;
    }
    
    # Protected with authentication
    location / {
        auth_basic "APT Repository";
        auth_basic_user_file $REPO_BASE/.htpasswd;
        
        # No directory listing for security
        autoindex off;
    }
    
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}
EOF
    else
        # Public: no auth, fancyindex on (styled directory listing)
        cat > /etc/nginx/sites-available/apt-repo << EOF
server {
    listen $LISTEN_PORT;
    server_name $DOMAIN;
    
    root $REPO_DIR;
    
    # Theme files
    location /.theme/ {
        alias $REPO_DIR/.theme/;
    }
    
    # Directory listing with fancyindex
    location / {
        fancyindex on;
        fancyindex_exact_size off;
        fancyindex_localtime on;
        fancyindex_header "/.theme/header.html";
        fancyindex_footer "/.theme/footer.html";
        fancyindex_time_format "%Y-%m-%d %H:%M";
        fancyindex_name_length 50;
    }
    
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}
EOF
    fi
    
    ln -sf /etc/nginx/sites-available/apt-repo /etc/nginx/sites-enabled/
    
    print_step "Nginx 테스트 및 재시작..."
    nginx -t
    systemctl enable nginx
    systemctl restart nginx
    
    print_success "Nginx 설정 완료 (포트 $LISTEN_PORT)"
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

LXC_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  APT 저장소 정보"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  URL:        https://$DOMAIN"
echo "  내부 IP:    $LXC_IP:$LISTEN_PORT"
echo "  저장소:     /var/www/apt/repo"
echo "  코드네임:   $REPO_CODENAME"
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
    
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        # Private 모드: 인증 지원
        cat > "$REPO_DIR/setup-client.sh" << CLIENTEOF
#!/bin/bash
set -e

DOMAIN="$DOMAIN"
AUTH_USER="\${1:?Usage: \$0 USERNAME PASSWORD}"
AUTH_PASS="\${2:?Usage: \$0 USERNAME PASSWORD}"
CODENAME="$REPO_CODENAME"

echo ""
echo "============================================================"
echo "  APT Repository Client Setup (Private)"
echo "============================================================"
echo ""
echo "  Domain:   \$DOMAIN"
echo "  Codename: \$CODENAME"
echo ""

# 1. Add GPG key
echo "[1/4] Adding GPG key..."
rm -f /usr/share/keyrings/internal-apt.gpg
curl -fsSL -u "\$AUTH_USER:\$AUTH_PASS" "https://\$DOMAIN/key.gpg" | \\
    gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
echo "      Done"

# 2. Configure authentication
echo "[2/4] Configuring authentication..."
mkdir -p /etc/apt/auth.conf.d
cat > /etc/apt/auth.conf.d/internal.conf << AUTHEOF
machine \$DOMAIN
login \$AUTH_USER
password \$AUTH_PASS
AUTHEOF
chmod 600 /etc/apt/auth.conf.d/internal.conf
echo "      Done"

# 3. Add APT source
echo "[3/4] Adding APT source..."
cat > /etc/apt/sources.list.d/internal.list << SRCEOF
deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://\$DOMAIN \$CODENAME main
SRCEOF
echo "      Done"

# 4. Update package list
echo "[4/4] Updating package list..."
apt-get update -qq

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo "============================================================"
echo ""
echo "  Install packages with:"
echo "    sudo apt install vaultctl"
echo ""
CLIENTEOF
    else
        # Public 모드: 인증 없음
        cat > "$REPO_DIR/setup-client.sh" << CLIENTEOF
#!/bin/bash
set -e

DOMAIN="$DOMAIN"
CODENAME="$REPO_CODENAME"

echo ""
echo "============================================================"
echo "  APT Repository Client Setup"
echo "============================================================"
echo ""
echo "  Domain:   \$DOMAIN"
echo "  Codename: \$CODENAME"
echo ""

# 1. Add GPG key
echo "[1/3] Adding GPG key..."
rm -f /usr/share/keyrings/internal-apt.gpg
curl -fsSL "https://\$DOMAIN/key.gpg" | \\
    gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg
echo "      Done"

# 2. Add APT source
echo "[2/3] Adding APT source..."
cat > /etc/apt/sources.list.d/internal.list << SRCEOF
deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://\$DOMAIN \$CODENAME main
SRCEOF
echo "      Done"

# 3. Update package list
echo "[3/3] Updating package list..."
apt-get update -qq

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo "============================================================"
echo ""
echo "  Install packages with:"
echo "    sudo apt install vaultctl"
echo ""
CLIENTEOF
    fi
    chmod +x "$REPO_DIR/setup-client.sh"
    
    print_step "index.html 생성..."
    
    # 템플릿 파일 경로 (스크립트와 같은 위치 또는 templates 디렉토리)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    TEMPLATE_FILE=""
    
    # 템플릿 파일 찾기 (realpath로 경로 정규화)
    for path in "$SCRIPT_DIR/../templates/index.html" "$SCRIPT_DIR/templates/index.html" "/opt/vaultctl/templates/index.html"; do
        resolved_path="$(realpath "$path" 2>/dev/null || echo "")"
        if [[ -n "$resolved_path" ]] && [[ -f "$resolved_path" ]]; then
            TEMPLATE_FILE="$resolved_path"
            print_step "템플릿 사용: $TEMPLATE_FILE"
            break
        fi
    done
    
    if [[ -z "$TEMPLATE_FILE" ]]; then
        print_warning "템플릿 파일을 찾을 수 없음, 내장 HTML 사용"
        print_warning "SCRIPT_DIR: $SCRIPT_DIR"
    fi
    
    # Public/Private에 따른 플레이스홀더 값 설정
    if [[ "$ENABLE_AUTH" == "true" ]]; then
        AUTH_BADGE='<span class="badge">Private</span>'
        AUTH_COMMENT="with your credentials"
        AUTH_ARGS="-s -- USER PASSWORD"
        AUTH_CURL="-u USER:PASS"
        AUTH_SECTION='
<span class="comment"># 2. Configure authentication</span>
echo "machine __DOMAIN__ login USER password PASS" | \
    sudo tee /etc/apt/auth.conf.d/internal.conf
sudo chmod 600 /etc/apt/auth.conf.d/internal.conf
'
        AUTH_STEP="3"
        INSTALL_STEP="4"
    else
        AUTH_BADGE='<span class="badge" style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);">Public</span>'
        AUTH_COMMENT=""
        AUTH_ARGS=""
        AUTH_CURL=""
        AUTH_SECTION=""
        AUTH_STEP="2"
        INSTALL_STEP="3"
    fi
    
    if [[ -n "$TEMPLATE_FILE" ]]; then
        # 템플릿 복사 후 플레이스홀더 치환
        cp "$TEMPLATE_FILE" "$REPO_DIR/index.html"
        sed -i "s|__DOMAIN__|$DOMAIN|g" "$REPO_DIR/index.html"
        sed -i "s|__CODENAME__|$REPO_CODENAME|g" "$REPO_DIR/index.html"
        sed -i "s|__AUTH_BADGE__|$AUTH_BADGE|g" "$REPO_DIR/index.html"
        sed -i "s|__AUTH_COMMENT__|$AUTH_COMMENT|g" "$REPO_DIR/index.html"
        sed -i "s|__AUTH_ARGS__|$AUTH_ARGS|g" "$REPO_DIR/index.html"
        sed -i "s|__AUTH_CURL__|$AUTH_CURL|g" "$REPO_DIR/index.html"
        sed -i "s|__AUTH_STEP__|$AUTH_STEP|g" "$REPO_DIR/index.html"
        sed -i "s|__INSTALL_STEP__|$INSTALL_STEP|g" "$REPO_DIR/index.html"
        # AUTH_SECTION은 여러 줄이므로 별도 처리
        if [[ "$ENABLE_AUTH" == "true" ]]; then
            sed -i 's|__AUTH_SECTION__|<span class="comment">\# 2. Configure authentication</span>\necho "machine '"$DOMAIN"' login USER password PASS" \| \\\n    sudo tee /etc/apt/auth.conf.d/internal.conf\nsudo chmod 600 /etc/apt/auth.conf.d/internal.conf\n|g' "$REPO_DIR/index.html"
        else
            sed -i 's|__AUTH_SECTION__||g' "$REPO_DIR/index.html"
        fi
    else
        # 템플릿이 없으면 기본 HTML 생성 (shadcn/ui 스타일)
        if [[ "$ENABLE_AUTH" == "true" ]]; then
            cat > "$REPO_DIR/index.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APT Repository</title>
    <style>
        :root{--background:#fff;--foreground:#0a0a0a;--card:#fff;--muted:#f4f4f5;--muted-foreground:#71717a;--border:#e4e4e7;--radius:0.5rem}
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--background);color:var(--foreground);line-height:1.5;-webkit-font-smoothing:antialiased}
        .container{max-width:800px;margin:0 auto;padding:48px 24px}
        .header{margin-bottom:32px}
        .header h1{font-size:30px;font-weight:700;letter-spacing:-0.025em;margin-bottom:8px}
        .header p{color:var(--muted-foreground);font-size:14px}
        .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:24px}
        .card-header{margin-bottom:16px}
        .card-title{font-size:18px;font-weight:600}
        .card-description{font-size:14px;color:var(--muted-foreground);margin-top:4px}
        .btn{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;font-size:14px;font-weight:500;border-radius:var(--radius);border:1px solid var(--border);background:var(--background);color:var(--foreground);text-decoration:none;transition:background .15s,border-color .15s}
        .btn:hover{background:var(--muted);border-color:var(--foreground)}
        .btn-group{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:32px}
        .code-block{background:var(--foreground);color:#fafafa;border-radius:var(--radius);padding:16px 20px;overflow-x:auto;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:13px;line-height:1.7;white-space:pre-wrap;word-break:break-all}
        .code-block .c{color:#71717a}
        code{background:var(--muted);padding:2px 6px;border-radius:4px;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:13px}
        .info-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
        .info-item{padding:16px;background:var(--muted);border-radius:var(--radius)}
        .info-item dt{font-size:12px;color:var(--muted-foreground);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px}
        .info-item dd{font-size:14px;font-weight:500;font-family:ui-monospace,'SF Mono',Consolas,monospace}
        .separator{height:1px;background:var(--border);margin:32px 0}
        .footer{text-align:center;font-size:12px;color:var(--muted-foreground)}
        @media(max-width:640px){.info-grid{grid-template-columns:1fr}}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>APT Repository</h1>
            <p>Internal package distribution server</p>
        </div>
        <div class="info-grid">
            <dl class="info-item"><dt>Domain</dt><dd>DOMAIN_PLACEHOLDER</dd></dl>
            <dl class="info-item"><dt>Codename</dt><dd>CODENAME_PLACEHOLDER</dd></dl>
            <dl class="info-item"><dt>Architecture</dt><dd>amd64</dd></dl>
        </div>
        <div class="btn-group">
            <a href="/key.gpg" class="btn">GPG Key</a>
            <a href="/setup-client.sh" class="btn">Setup Script</a>
        </div>
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">Quick Setup</h2>
                <p class="card-description">One command to configure everything</p>
            </div>
            <div class="code-block">curl -fsSL https://DOMAIN_PLACEHOLDER/setup-client.sh | sudo bash -s -- USER PASSWORD</div>
        </div>
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">Manual Setup</h2>
                <p class="card-description">Step by step installation</p>
            </div>
            <div class="code-block"><span class="c"># Add GPG key</span>
curl -fsSL -u USER:PASS https://DOMAIN_PLACEHOLDER/key.gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

<span class="c"># Configure auth</span>
echo "machine DOMAIN_PLACEHOLDER login USER password PASS" | \
    sudo tee /etc/apt/auth.conf.d/internal.conf
sudo chmod 600 /etc/apt/auth.conf.d/internal.conf

<span class="c"># Add APT source</span>
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://DOMAIN_PLACEHOLDER CODENAME_PLACEHOLDER main" | \
    sudo tee /etc/apt/sources.list.d/internal.list

<span class="c"># Install</span>
sudo apt update
sudo apt install vaultctl</div>
        </div>
        <div class="separator"></div>
        <div class="footer">Powered by reprepro + GPG signing</div>
    </div>
</body>
</html>
HTMLEOF
        else
            cat > "$REPO_DIR/index.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APT Repository</title>
    <style>
        :root{--background:#fff;--foreground:#0a0a0a;--card:#fff;--muted:#f4f4f5;--muted-foreground:#71717a;--border:#e4e4e7;--radius:0.5rem}
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--background);color:var(--foreground);line-height:1.5;-webkit-font-smoothing:antialiased}
        .container{max-width:800px;margin:0 auto;padding:48px 24px}
        .header{margin-bottom:32px}
        .header h1{font-size:30px;font-weight:700;letter-spacing:-0.025em;margin-bottom:8px}
        .header p{color:var(--muted-foreground);font-size:14px}
        .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:24px}
        .card-header{margin-bottom:16px}
        .card-title{font-size:18px;font-weight:600}
        .card-description{font-size:14px;color:var(--muted-foreground);margin-top:4px}
        .btn{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;font-size:14px;font-weight:500;border-radius:var(--radius);border:1px solid var(--border);background:var(--background);color:var(--foreground);text-decoration:none;transition:background .15s,border-color .15s}
        .btn:hover{background:var(--muted);border-color:var(--foreground)}
        .btn-group{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:32px}
        .code-block{background:var(--foreground);color:#fafafa;border-radius:var(--radius);padding:16px 20px;overflow-x:auto;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:13px;line-height:1.7;white-space:pre-wrap;word-break:break-all}
        .code-block .c{color:#71717a}
        code{background:var(--muted);padding:2px 6px;border-radius:4px;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:13px}
        .info-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
        .info-item{padding:16px;background:var(--muted);border-radius:var(--radius)}
        .info-item dt{font-size:12px;color:var(--muted-foreground);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px}
        .info-item dd{font-size:14px;font-weight:500;font-family:ui-monospace,'SF Mono',Consolas,monospace}
        .separator{height:1px;background:var(--border);margin:32px 0}
        .footer{text-align:center;font-size:12px;color:var(--muted-foreground)}
        @media(max-width:640px){.info-grid{grid-template-columns:1fr}}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>APT Repository</h1>
            <p>Internal package distribution server</p>
        </div>
        <div class="info-grid">
            <dl class="info-item"><dt>Domain</dt><dd>DOMAIN_PLACEHOLDER</dd></dl>
            <dl class="info-item"><dt>Codename</dt><dd>CODENAME_PLACEHOLDER</dd></dl>
            <dl class="info-item"><dt>Architecture</dt><dd>amd64</dd></dl>
        </div>
        <div class="btn-group">
            <a href="/key.gpg" class="btn">GPG Key</a>
            <a href="/setup-client.sh" class="btn">Setup Script</a>
        </div>
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">Quick Setup</h2>
                <p class="card-description">One command to configure everything</p>
            </div>
            <div class="code-block">curl -fsSL https://DOMAIN_PLACEHOLDER/setup-client.sh | sudo bash</div>
        </div>
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">Manual Setup</h2>
                <p class="card-description">Step by step installation</p>
            </div>
            <div class="code-block"><span class="c"># Add GPG key</span>
curl -fsSL https://DOMAIN_PLACEHOLDER/key.gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

<span class="c"># Add APT source</span>
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://DOMAIN_PLACEHOLDER CODENAME_PLACEHOLDER main" | \
    sudo tee /etc/apt/sources.list.d/internal.list

<span class="c"># Install</span>
sudo apt update
sudo apt install vaultctl</div>
        </div>
        <div class="separator"></div>
        <div class="footer">Powered by reprepro + GPG signing</div>
    </div>
</body>
</html>
HTMLEOF
        fi
        sed -i "s|DOMAIN_PLACEHOLDER|$DOMAIN|g" "$REPO_DIR/index.html"
        sed -i "s|CODENAME_PLACEHOLDER|$REPO_CODENAME|g" "$REPO_DIR/index.html"
    fi
    
    # 파일 권한 설정
    chown -R www-data:www-data "$REPO_DIR"
    
    print_success "클라이언트 스크립트 생성 완료"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Traefik 설정 안내
# ═══════════════════════════════════════════════════════════════════════════════

print_traefik_config() {
    print_header "Traefik 라우팅 설정"
    
    LXC_IP=$(hostname -I | awk '{print $1}')
    
    echo "Traefik에 다음 라우팅을 추가하세요:"
    echo ""
    echo -e "${CYAN}Docker labels 방식:${NC}"
    cat << EOF
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.apt-repo.rule=Host(\`$DOMAIN\`)"
  - "traefik.http.routers.apt-repo.entrypoints=websecure"
  - "traefik.http.routers.apt-repo.tls.certresolver=letsencrypt"
  - "traefik.http.services.apt-repo.loadbalancer.server.url=http://$LXC_IP:$LISTEN_PORT"
EOF
    
    echo ""
    echo -e "${CYAN}파일 기반 설정:${NC}"
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
    echo "  코드네임:   $REPO_CODENAME"
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
    echo "  1. Traefik에 라우팅 추가 (위 설정 참고)"
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
    echo -e "${BOLD}║     APT 저장소 서버 설치 (Traefik 환경)                   ║${NC}"
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

        # 기존 GPG 키 ID 조회
        export GNUPGHOME="$GPG_HOME"
        GPG_KEY_ID=$(gpg --list-keys --keyid-format SHORT 2>/dev/null | grep -E '^\s+[A-F0-9]+' | awk '{print $1}' | head -1)
        if [[ -z "$GPG_KEY_ID" ]]; then
            GPG_KEY_ID=$(gpg --list-keys --keyid-format LONG 2>/dev/null | grep -oP '[A-F0-9]{16}' | head -1)
            GPG_KEY_ID="${GPG_KEY_ID: -8}"
        fi
        if [[ -z "$GPG_KEY_ID" ]]; then
            print_error "GPG 키를 찾을 수 없습니다. 전체 설치를 진행하세요."
            exit 1
        fi
        export GPG_KEY_ID
        print_step "GPG 키 ID: $GPG_KEY_ID"

        # GPG 키 파일 이름 마이그레이션 (KEY.gpg -> key.gpg)
        if [[ -f "$REPO_DIR/KEY.gpg" ]] && [[ ! -f "$REPO_DIR/key.gpg" ]]; then
            print_step "GPG 키 파일 이름 변경 (KEY.gpg -> key.gpg)..."
            mv "$REPO_DIR/KEY.gpg" "$REPO_DIR/key.gpg"
            mv "$REPO_DIR/KEY" "$REPO_DIR/key" 2>/dev/null || true
        fi
        setup_reprepro
        setup_auth
        setup_nginx
        create_client_scripts
        print_summary
    else
        install_packages
        setup_directories
        setup_gpg
        save_config
        setup_reprepro
        setup_auth
        setup_nginx
        create_management_scripts
        create_client_scripts
        print_traefik_config
        print_summary
    fi
}

main "$@"