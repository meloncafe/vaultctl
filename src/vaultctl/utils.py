"""유틸리티 함수 및 템플릿 렌더링."""

import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.table import Table

console = Console()


# ═══════════════════════════════════════════════════════════════════════════════
# General Utilities / 일반 유틸리티
# ═══════════════════════════════════════════════════════════════════════════════


def copy_to_clipboard(text: str) -> bool:
    """텍스트를 클립보드에 복사."""
    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            subprocess.run(
                ["pbcopy"],
                input=text.encode(),
                check=True,
                timeout=5,
            )
            return True
        elif system == "Linux":
            # xclip 시도
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
            # xsel 시도
            try:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
            # wl-copy 시도 (Wayland)
            try:
                subprocess.run(
                    ["wl-copy"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
        elif system == "Windows":
            subprocess.run(
                ["clip"],
                input=text.encode(),
                check=True,
                timeout=5,
                shell=True,
            )
            return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    return False


def format_duration(seconds: int) -> str:
    """초를 사람이 읽기 좋은 형식으로 변환."""
    if seconds <= 0:
        return "만료됨"

    delta = timedelta(seconds=seconds)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    if secs > 0 and not parts:
        parts.append(f"{secs}초")

    return " ".join(parts) if parts else "곧 만료"


def format_timestamp(ts: Optional[str]) -> str:
    """ISO 타임스탬프를 로컬 시간으로 변환."""
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts


def create_kv_table(data: dict, title: str = "Secrets") -> Table:
    """KV 데이터를 Rich 테이블로 변환."""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Key", style="green")
    table.add_column("Value", style="white")

    for key, value in sorted(data.items()):
        # 비밀번호 등 민감한 필드는 마스킹
        display_value = str(value)
        if any(
            sensitive in key.lower()
            for sensitive in ["password", "secret", "token", "key", "credential"]
        ):
            if len(display_value) > 4:
                display_value = display_value[:2] + "*" * (len(display_value) - 4) + display_value[-2:]
            else:
                display_value = "*" * len(display_value)

        table.add_row(key, display_value)

    return table


def parse_key_value_args(args: list[str]) -> dict[str, str]:
    """key=value 형식의 인자를 딕셔너리로 변환."""
    result = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def load_env_file(path: str) -> dict[str, str]:
    """환경변수 파일 로드."""
    result = {}
    if not os.path.exists(path):
        return result

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            # 주석, 빈 줄 무시
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                # 따옴표 제거
                value = value.strip().strip("'\"")
                result[key.strip()] = value

    return result


def write_env_file(path: str, data: dict[str, str], header: Optional[str] = None) -> None:
    """환경변수 파일 저장."""
    with open(path, "w") as f:
        if header:
            f.write(f"# {header}\n")
        f.write(f"# Generated at: {datetime.now().isoformat()}\n\n")
        for key, value in sorted(data.items()):
            # Ensure value is string
            value = str(value)
            # 특수문자가 있으면 따옴표로 감싸기
            if any(c in value for c in [" ", "'", '"', "$", "\n"]):
                value = f'"{value}"'
            f.write(f"{key}={value}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Template Rendering / 템플릿 렌더링
# ═══════════════════════════════════════════════════════════════════════════════


def _get_templates_dir() -> Path:
    """Get templates directory, handling PyInstaller bundle.
    PyInstaller 번들을 처리하여 템플릿 디렉토리 반환.
    """
    # PyInstaller bundle / PyInstaller 번들
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / "vaultctl" / "templates"
    # Normal execution / 일반 실행
    return Path(__file__).parent / "templates"


# Template directory path / 템플릿 디렉토리 경로
TEMPLATES_DIR = _get_templates_dir()


def get_jinja_env() -> Environment:
    """Get Jinja2 environment with configured loaders.
    설정된 로더로 Jinja2 환경 반환.
    """
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_template(template_name: str, context: Dict[str, Any]) -> str:
    """Render a template with the given context.
    주어진 컨텍스트로 템플릿 렌더링.
    
    Args:
        template_name: Template file name (e.g., "apt/Caddyfile.j2")
        context: Dictionary of variables to pass to template
        
    Returns:
        Rendered template string
    """
    env = get_jinja_env()
    template = env.get_template(template_name)
    
    # Add common context variables / 공통 컨텍스트 변수 추가
    context.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    return template.render(**context)


# ═══════════════════════════════════════════════════════════════════════════════
# APT Server Templates / APT 서버 템플릿
# ═══════════════════════════════════════════════════════════════════════════════


def render_caddyfile(
    domain: str,
    repo_path: str,
    enable_auth: bool = True,
    auth_user: Optional[str] = None,
    caddy_hash: Optional[str] = None,
) -> str:
    """Render Caddy configuration file.
    Caddy 설정 파일 렌더링.
    """
    return render_template("apt/Caddyfile.j2", {
        "domain": domain,
        "repo_path": repo_path,
        "enable_auth": enable_auth,
        "auth_user": auth_user,
        "caddy_hash": caddy_hash,
    })


def render_nginx_conf(
    domain: str,
    repo_path: str,
    listen_port: int = 8080,
    enable_auth: bool = True,
    htpasswd_path: Optional[str] = None,
) -> str:
    """Render Nginx configuration file.
    Nginx 설정 파일 렌더링.
    """
    return render_template("apt/nginx.conf.j2", {
        "domain": domain,
        "repo_path": repo_path,
        "listen_port": listen_port,
        "enable_auth": enable_auth,
        "htpasswd_path": htpasswd_path,
    })


def render_reprepro_distributions(
    repo_name: str,
    repo_label: str,
    repo_codename: str,
    repo_arch: str,
    gpg_key_id: str,
) -> str:
    """Render reprepro distributions file.
    reprepro distributions 파일 렌더링.
    """
    return render_template("apt/distributions.j2", {
        "repo_name": repo_name,
        "repo_label": repo_label,
        "repo_codename": repo_codename,
        "repo_arch": repo_arch,
        "gpg_key_id": gpg_key_id,
    })


def render_reprepro_options(
    repo_path: str,
    gpg_home: str,
) -> str:
    """Render reprepro options file.
    reprepro options 파일 렌더링.
    """
    return render_template("apt/options.j2", {
        "repo_path": repo_path,
        "gpg_home": gpg_home,
    })


def render_setup_client_script(
    domain: str,
    repo_codename: str,
    enable_auth: bool = True,
) -> str:
    """Render client setup script.
    클라이언트 설치 스크립트 렌더링.
    """
    return render_template("apt/setup-client.sh.j2", {
        "domain": domain,
        "repo_codename": repo_codename,
        "enable_auth": enable_auth,
    })


def render_index_html(
    domain: str,
    repo_codename: str,
    repo_arch: str,
    enable_auth: bool = True,
) -> str:
    """Render repository index.html.
    저장소 index.html 렌더링.
    """
    return render_template("apt/index.html.j2", {
        "domain": domain,
        "repo_codename": repo_codename,
        "repo_arch": repo_arch,
        "enable_auth": enable_auth,
    })


def render_fancyindex_header(
    domain: str,
    enable_auth: bool = True,
) -> str:
    """Render nginx fancyindex header.html.
    nginx fancyindex header.html 렌더링.
    """
    return render_template("apt/fancyindex-header.html.j2", {
        "domain": domain,
        "enable_auth": enable_auth,
    })


def render_fancyindex_footer() -> str:
    """Render nginx fancyindex footer.html.
    nginx fancyindex footer.html 렌더링.
    """
    return render_template("apt/fancyindex-footer.html.j2", {})


def render_apt_config(config: Dict[str, str]) -> str:
    """Render APT repository configuration file.
    APT 저장소 설정 파일 렌더링.
    """
    return render_template("apt/apt-config.j2", {
        "domain": config.get("DOMAIN", ""),
        "gpg_email": config.get("GPG_EMAIL", ""),
        "gpg_name": config.get("GPG_NAME", ""),
        "repo_name": config.get("REPO_NAME", ""),
        "repo_label": config.get("REPO_LABEL", ""),
        "repo_codename": config.get("REPO_CODENAME", ""),
        "repo_arch": config.get("REPO_ARCH", ""),
        "enable_auth": config.get("ENABLE_AUTH", "false"),
        "auth_user": config.get("AUTH_USER", ""),
        "auth_pass": config.get("AUTH_PASS", ""),
        "web_server": config.get("WEB_SERVER", ""),
        "listen_port": config.get("LISTEN_PORT", "8080"),
    })


def render_gpg_batch(
    gpg_name: str,
    gpg_email: str,
) -> str:
    """Render GPG key generation batch file.
    GPG 키 생성 배치 파일 렌더링.
    """
    return render_template("apt/gpg-batch.j2", {
        "gpg_name": gpg_name,
        "gpg_email": gpg_email,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl Config Templates / vaultctl 설정 템플릿
# ═══════════════════════════════════════════════════════════════════════════════


def render_vaultctl_config(
    vault_addr: str,
    vault_token: Optional[str] = None,
    role_id: Optional[str] = None,
    secret_id: Optional[str] = None,
) -> str:
    """Render vaultctl configuration file.
    vaultctl 설정 파일 렌더링.
    """
    use_approle = bool(role_id and secret_id)
    
    return render_template("config.j2", {
        "vault_addr": vault_addr,
        "use_approle": use_approle,
        "role_id": role_id or "",
        "secret_id": secret_id or "",
        "vault_token": vault_token or "",
    })
