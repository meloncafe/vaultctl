"""Setup commands for vaultctl.
vaultctl 설정 관련 명령어.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from vaultctl.commands.auth import ensure_authenticated
from vaultctl.config import settings
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(help="Initial setup and systemd management / 초기 설정 및 systemd 관리")
console = Console()

# Constants / 상수
CONFIG_DIR = Path("/etc/vaultctl")
CONFIG_FILE = CONFIG_DIR / "config"
CONFIG_EXAMPLE = CONFIG_DIR / "config.example"


# ═══════════════════════════════════════════════════════════════════════════════
# Init Command / 초기 설정 명령어
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("init")
def init_setup(
    vault_addr: Optional[str] = typer.Option(
        None,
        "--vault-addr",
        "-a",
        help="Vault server address / Vault 서버 주소",
    ),
    use_approle: bool = typer.Option(
        False,
        "--approle",
        help="Use AppRole authentication (recommended) / AppRole 인증 사용 (권장)",
    ),
    use_token: bool = typer.Option(
        False,
        "--token",
        help="Use direct token authentication / 토큰 직접 입력 사용",
    ),
    setup_timer: bool = typer.Option(
        True,
        "--timer/--no-timer",
        help="Setup systemd timer / systemd 타이머 설정",
    ),
):
    """vaultctl initial setup wizard.
    vaultctl 초기 설정 마법사.

    Interactively configures Vault connection, authentication, and systemd timer.
    대화형으로 Vault 연결, 인증, systemd 타이머를 설정합니다.
    
    Authentication methods / 인증 방법:
    - AppRole (recommended): Auto-renews token on expiry / 토큰 만료 시 자동 재발급
    - Token: Manual renewal required / 만료 시 수동 갱신 필요
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl Initial Setup[/bold blue]\n\n"
        "This wizard will configure:\n"
        "• Vault server connection\n"
        "• Authentication (AppRole or Token)\n"
        "• systemd auto-renewal timer",
        title="🔐 Setup Wizard",
    ))
    console.print()

    # 1. Vault address / Vault 주소 설정
    if not vault_addr:
        vault_addr = Prompt.ask(
            "Vault server address / Vault 서버 주소",
            default=settings.vault_addr,
        )

    # Connection test / 연결 테스트
    console.print(f"\n[dim]Testing connection: {vault_addr}[/dim]")
    client = VaultClient(addr=vault_addr)
    health = client.health()

    if not health.get("initialized"):
        console.print("[red]✗[/red] Cannot connect to Vault server or not initialized.")
        raise typer.Exit(1)

    if health.get("sealed"):
        console.print("[red]✗[/red] Vault server is sealed.")
        raise typer.Exit(1)

    console.print("[green]✓[/green] Connection successful")

    # 2. Authentication method / 인증 방법 선택
    if not use_approle and not use_token:
        console.print("\n[bold]Select authentication method[/bold]")
        console.print("  1. AppRole (recommended) - Auto-renews on expiry")
        console.print("  2. Token - Manual input (requires manual renewal)")
        
        choice = Prompt.ask(
            "\nChoice / 선택",
            choices=["1", "2"],
            default="1",
        )
        use_approle = choice == "1"
        use_token = choice == "2"

    vault_token = None
    role_id = None
    secret_id = None

    if use_approle:
        # AppRole authentication / AppRole 인증
        console.print("\n[bold]AppRole Authentication Setup[/bold]")
        console.print("[dim]Enter Role ID and Secret ID from your Vault administrator.[/dim]")
        
        role_id = Prompt.ask("Role ID")
        secret_id = Prompt.ask("Secret ID", password=True)
        
        # Test AppRole login / AppRole 로그인 테스트
        console.print("\n[dim]Testing AppRole authentication...[/dim]")
        try:
            result = client.approle_login(role_id, secret_id, settings.approle_mount)
            vault_token = result.get("auth", {}).get("client_token")
            
            if not vault_token:
                console.print("[red]✗[/red] No token in AppRole login response.")
                raise typer.Exit(1)
            
            console.print("[green]✓[/green] AppRole authentication successful")
            
            auth_data = result.get("auth", {})
            console.print(f"  Policies: {', '.join(auth_data.get('policies', []))}")
            ttl = auth_data.get("lease_duration", 0)
            console.print(f"  TTL: {ttl}s ({ttl // 3600}h)")
            
        except VaultError as e:
            console.print(f"[red]✗[/red] AppRole authentication failed: {e.message}")
            raise typer.Exit(1)
    
    else:
        # Direct token / 토큰 직접 입력
        vault_token = Prompt.ask(
            "\nEnter Vault token",
            password=True,
        )

        # Validate token / 토큰 검증
        client = VaultClient(addr=vault_addr, token=vault_token)
        try:
            token_info = client.token_lookup()
            console.print("[green]✓[/green] Token validation successful")

            data = token_info.get("data", {})
            console.print(f"  Policies: {', '.join(data.get('policies', []))}")
            ttl = data.get("ttl", 0)
            console.print(f"  TTL: {'unlimited' if ttl == 0 else f'{ttl}s ({ttl // 3600}h)'}")
            
            if ttl > 0 and ttl < 86400:
                console.print("[yellow]![/yellow] TTL is short. Consider using AppRole.")
                
        except VaultError as e:
            console.print(f"[red]✗[/red] Token validation failed: {e.message}")
            raise typer.Exit(1)

    # 3. Create config file / 환경 파일 생성
    if CONFIG_FILE.exists():
        if not Confirm.ask(f"\n{CONFIG_FILE} already exists. Overwrite?"):
            console.print("[dim]Keeping existing config[/dim]")
        else:
            _write_config_file(CONFIG_FILE, vault_addr, vault_token, role_id, secret_id)
    else:
        if os.geteuid() == 0:
            _write_config_file(CONFIG_FILE, vault_addr, vault_token, role_id, secret_id)
        else:
            console.print("\n[yellow]![/yellow] Root privilege required to create config file.")
            console.print("  Manual creation:")
            console.print(f"    sudo cp {CONFIG_EXAMPLE} {CONFIG_FILE}")
            console.print(f"    sudo chmod 600 {CONFIG_FILE}")
            console.print(f"    sudo nano {CONFIG_FILE}")

    # 4. systemd timer setup / systemd 타이머 설정
    if setup_timer:
        console.print("\n[bold]systemd Timer Setup[/bold]")

        if os.geteuid() != 0:
            console.print("[yellow]![/yellow] Root privilege required.")
            console.print("  Manual activation:")
            console.print("    sudo systemctl enable --now vaultctl-renew.timer")
        else:
            if Confirm.ask("Enable auto-renewal timer?"):
                _setup_systemd_timer()

    # 5. Complete / 완료
    console.print("\n")
    
    if use_approle:
        console.print(Panel.fit(
            "[bold green]Setup Complete![/bold green]\n\n"
            "AppRole authentication configured.\n"
            "Token will auto-renew on expiry.\n\n"
            "Try these commands:\n"
            "  vaultctl auth status    # Check auth status\n"
            "  vaultctl lxc list       # List LXC\n"
            "  vaultctl --help         # Full help",
            title="✓ Complete",
        ))
    else:
        console.print(Panel.fit(
            "[bold green]Setup Complete![/bold green]\n\n"
            "Token authentication configured.\n"
            "[yellow]Manual renewal required on expiry.[/yellow]\n\n"
            "Try these commands:\n"
            "  vaultctl auth status    # Check auth status\n"
            "  vaultctl lxc list       # List LXC\n"
            "  vaultctl --help         # Full help",
            title="✓ Complete",
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# APT Server Command / APT 서버 명령어
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("apt-server")
def apt_server_setup(
    reconfigure: bool = typer.Option(False, "--reconfigure", "-r", help="Reconfigure only"),
):
    """Setup APT repository server (interactive).
    APT 저장소 서버 구축 (대화형).

    Supports two web server modes:
    - Caddy: Standalone with automatic HTTPS via Let's Encrypt
    - Traefik: Backend for existing Traefik reverse proxy

    Examples:
        sudo vaultctl setup apt-server
        sudo vaultctl setup apt-server --reconfigure
    """
    _check_root("setup apt-server")
    
    # Load existing config / 기존 설정 로드
    apt_config = _load_apt_config()
    
    if reconfigure and not apt_config:
        console.print("[red]✗[/red] APT repository not installed. Run full setup first.")
        raise typer.Exit(1)
    
    # Show existing configuration if found / 기존 설정이 있으면 표시
    if apt_config:
        console.print("[yellow]Existing configuration found[/yellow]")
        console.print(f"  Domain: {apt_config.get('DOMAIN', 'N/A')}")
        console.print(f"  GPG Email: {apt_config.get('GPG_EMAIL', 'N/A')}")
        console.print(f"  Web Server: {apt_config.get('WEB_SERVER', 'N/A').upper()}")
        console.print()
    
    # Web server selection / 웹서버 선택
    console.print("[bold]Select web server mode[/bold]")
    console.print("  1. Caddy - Standalone with automatic HTTPS (Let's Encrypt)")
    console.print("  2. Traefik - Backend for existing Traefik reverse proxy")
    
    existing_ws = apt_config.get("WEB_SERVER", "caddy")
    default_ws = "1" if existing_ws == "caddy" else "2"
    ws_choice = Prompt.ask("\nChoice / 선택", choices=["1", "2"], default=default_ws)
    web_server = "caddy" if ws_choice == "1" else "traefik"
    
    console.print()
    console.print(Panel.fit(
        f"[bold blue]APT Repository Server Setup[/bold blue]\n\n"
        f"Web server: {web_server.upper()}\n"
        f"{'Reconfiguration mode' if reconfigure else 'Full installation'}",
        title="📦 APT Server",
    ))
    console.print()
    
    # Domain / 도메인
    domain = Prompt.ask(
        "Domain (e.g., apt.example.com)",
        default=apt_config.get("DOMAIN", ""),
    )
    if not domain:
        console.print("[red]✗[/red] Domain is required.")
        raise typer.Exit(1)
    
    # GPG Email / GPG 이메일
    gpg_email = Prompt.ask(
        "GPG signing email (e.g., apt@example.com)",
        default=apt_config.get("GPG_EMAIL", ""),
    )
    if not gpg_email:
        console.print("[red]✗[/red] GPG email is required.")
        raise typer.Exit(1)
    
    # GPG Name / GPG 이름
    gpg_name = Prompt.ask(
        "GPG key name",
        default=apt_config.get("GPG_NAME", "APT Repository Signing Key"),
    )
    
    # Repository settings / 저장소 설정
    repo_name = Prompt.ask(
        "Repository name (Origin)",
        default=apt_config.get("REPO_NAME", "internal"),
    )
    repo_codename = Prompt.ask(
        "Distribution codename",
        default=apt_config.get("REPO_CODENAME", "stable"),
    )
    repo_arch = Prompt.ask(
        "Architecture",
        default=apt_config.get("REPO_ARCH", "amd64"),
    )
    
    # Authentication / 인증 설정
    enable_auth = Confirm.ask(
        "Enable authentication?",
        default=apt_config.get("ENABLE_AUTH", "true") == "true",
    )
    
    if enable_auth:
        auth_user = Prompt.ask(
            "Auth username",
            default=apt_config.get("AUTH_USER", "apt"),
        )
        
        existing_pass = apt_config.get("AUTH_PASS", "")
        console.print("[dim]Auth password (Enter to auto-generate or keep existing)[/dim]")
        auth_pass = Prompt.ask("Password", password=True, default="")
        
        if not auth_pass:
            if existing_pass:
                auth_pass = existing_pass
                console.print("  [green]Keeping existing password[/green]")
            else:
                import secrets
                auth_pass = secrets.token_urlsafe(16)
                console.print("  [green]Auto-generated password[/green]")
    else:
        auth_user = ""
        auth_pass = ""
    
    # Port for traefik mode / Traefik 모드용 포트
    listen_port = 8080  # Default port
    if web_server == "traefik":
        listen_port = int(Prompt.ask(
            "Nginx listen port (for Traefik backend)",
            default=str(apt_config.get("LISTEN_PORT", "8080")),
        ))
    
    # Confirmation / 설정 확인
    console.print("\n[bold]Configuration Summary[/bold]")
    table = Table(show_header=False, box=None)
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    table.add_row("Domain", domain)
    table.add_row("GPG Email", gpg_email)
    table.add_row("Repository", repo_name)
    table.add_row("Codename", repo_codename)
    table.add_row("Web Server", web_server.upper())
    table.add_row("Auth", f"{'Enabled' if enable_auth else 'Disabled'}")
    if enable_auth:
        table.add_row("Username", auth_user)
        table.add_row("Password", "********")
    if web_server == "traefik":
        table.add_row("Listen Port", str(listen_port))
    console.print(table)
    
    if not Confirm.ask("\nProceed with this configuration?"):
        console.print("[dim]Cancelled[/dim]")
        raise typer.Exit(0)
    
    # Execute setup / 설정 실행
    config = {
        "DOMAIN": domain,
        "GPG_EMAIL": gpg_email,
        "GPG_NAME": gpg_name,
        "REPO_NAME": repo_name,
        "REPO_LABEL": f"{repo_name.title()} Repository",
        "REPO_CODENAME": repo_codename,
        "REPO_ARCH": repo_arch,
        "ENABLE_AUTH": str(enable_auth).lower(),
        "AUTH_USER": auth_user,
        "AUTH_PASS": auth_pass,
        "WEB_SERVER": web_server,
        "LISTEN_PORT": str(listen_port),
    }
    
    if reconfigure:
        _apt_reconfigure(config, web_server)
    else:
        _apt_full_install(config, web_server)
    
    # Summary / 요약
    _print_apt_summary(config, web_server)


@app.command("apt-client")
def apt_client_setup(
    url: str = typer.Argument(..., help="APT repository URL (e.g., https://apt.example.com)"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Auth username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Auth password"),
    codename: str = typer.Option("stable", "--codename", "-c", help="Distribution codename"),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove APT source"),
):
    """Setup APT client to use repository.
    APT 클라이언트 설정.

    Examples:
        vaultctl setup apt-client https://apt.example.com
        vaultctl setup apt-client https://apt.example.com -u apt -p secret
        vaultctl setup apt-client https://apt.example.com --remove
    """
    _check_root("setup apt-client")
    
    # Extract domain from URL / URL에서 도메인 추출
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    
    if remove:
        _apt_client_remove(domain)
        return
    
    console.print(Panel.fit(
        f"[bold blue]APT Client Setup[/bold blue]\n\n"
        f"Repository: {url}\n"
        f"Codename: {codename}",
        title="📦 APT Client",
    ))
    
    # Check if auth is needed / 인증 필요 여부 확인
    if user and not password:
        password = Prompt.ask("Password", password=True)
    
    # 1. Add GPG key / GPG 키 추가
    console.print("\n[1/4] Adding GPG key...")
    keyring_path = Path("/usr/share/keyrings/internal-apt.gpg")
    keyring_path.unlink(missing_ok=True)
    
    curl_cmd = ["curl", "-fsSL"]
    if user and password:
        curl_cmd.extend(["-u", f"{user}:{password}"])
    curl_cmd.append(f"{url}/key.gpg")
    
    try:
        result = subprocess.run(curl_cmd, capture_output=True, check=True)
        gpg_result = subprocess.run(
            ["gpg", "--dearmor", "-o", str(keyring_path)],
            input=result.stdout,
            check=True,
        )
        console.print("      [green]✓[/green] Done")
    except subprocess.CalledProcessError as e:
        console.print(f"      [red]✗[/red] Failed to add GPG key: {e}")
        raise typer.Exit(1)
    
    # 2. Configure authentication / 인증 설정
    if user and password:
        console.print("[2/4] Configuring authentication...")
        auth_dir = Path("/etc/apt/auth.conf.d")
        auth_dir.mkdir(parents=True, exist_ok=True)
        auth_file = auth_dir / "internal.conf"
        auth_file.write_text(f"machine {domain}\nlogin {user}\npassword {password}\n")
        auth_file.chmod(0o600)
        console.print("      [green]✓[/green] Done")
    else:
        console.print("[2/4] Skipping authentication (public repo)")
    
    # 3. Add APT source / APT 소스 추가
    console.print("[3/4] Adding APT source...")
    sources_file = Path("/etc/apt/sources.list.d/internal.list")
    sources_file.write_text(
        f"deb [signed-by={keyring_path}] {url} {codename} main\n"
    )
    console.print("      [green]✓[/green] Done")
    
    # 4. Update package list / 패키지 목록 업데이트
    console.print("[4/4] Updating package list...")
    try:
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        console.print("      [green]✓[/green] Done")
    except subprocess.CalledProcessError:
        console.print("      [yellow]![/yellow] Update failed, but source was added")
    
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Install packages with:\n"
        "  sudo apt install vaultctl",
        title="✓ Complete",
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# systemd Command / systemd 명령어
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("systemd")
def systemd_setup(
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable/disable timer"),
    status: bool = typer.Option(False, "--status", "-s", help="Show timer status"),
):
    """Manage systemd timer.
    systemd 타이머 관리.

    Enable/disable or check status of the auto-renewal timer.
    토큰 자동 갱신 타이머를 활성화/비활성화하거나 상태를 확인합니다.
    """
    if status:
        _show_systemd_status()
        return

    if enable is None:
        _show_systemd_status()
        enable = Confirm.ask("\nEnable timer?")

    if os.geteuid() != 0:
        console.print("[red]✗[/red] Root privilege required.")
        if enable:
            console.print("  Run: sudo vaultctl setup systemd --enable")
        else:
            console.print("  Run: sudo vaultctl setup systemd --disable")
        raise typer.Exit(1)

    if enable:
        _setup_systemd_timer()
    else:
        _disable_systemd_timer()


@app.command("config")
def show_config(
    edit: bool = typer.Option(False, "--edit", "-e", help="Edit config file"),
):
    """Manage configuration file.
    환경 설정 파일 관리.
    """
    if edit:
        if os.geteuid() != 0:
            console.print("[red]✗[/red] Root privilege required.")
            console.print("  Run: sudo vaultctl setup config --edit")
            raise typer.Exit(1)

        if not CONFIG_FILE.exists() and CONFIG_EXAMPLE.exists():
            console.print("[dim]Copying from config.example...[/dim]")
            shutil.copy(CONFIG_EXAMPLE, CONFIG_FILE)
            CONFIG_FILE.chmod(0o600)

        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(CONFIG_FILE)])
        return

    # Show current config / 현재 설정 출력
    console.print("[bold]Configuration Files[/bold]")
    console.print(f"  Config: {CONFIG_FILE} {'[green](exists)[/green]' if CONFIG_FILE.exists() else '[red](missing)[/red]'}")
    console.print(f"  Example: {CONFIG_EXAMPLE} {'[green](exists)[/green]' if CONFIG_EXAMPLE.exists() else '[red](missing)[/red]'}")

    if CONFIG_FILE.exists():
        console.print("\n[bold]Current Settings[/bold]")
        content = CONFIG_FILE.read_text()
        for line in content.splitlines():
            if line.strip() and not line.startswith("#"):
                key = line.split("=")[0]
                if "TOKEN" in key or "SECRET" in key or "PASSWORD" in key or "PASS" in key:
                    console.print(f"  {key}=****")
                else:
                    console.print(f"  {line}")
    else:
        console.print("\n[yellow]![/yellow] Config file not found.")
        console.print("  Create: sudo vaultctl setup config --edit")


@app.command("test")
def test_connection():
    """Test Vault connection and authentication.
    Vault 연결 및 인증 테스트.
    """
    console.print("[bold]Connection Test[/bold]\n")

    # 1. Server connection / 서버 연결
    console.print(f"1. Vault server: {settings.vault_addr}")
    client = VaultClient()
    health = client.health()

    if health.get("initialized") and not health.get("sealed"):
        console.print("   [green]✓[/green] Connection successful")
    else:
        console.print("   [red]✗[/red] Connection failed")
        raise typer.Exit(1)

    # 2. Authentication / 인증
    console.print("\n2. Authentication")
    try:
        client = ensure_authenticated()
        console.print("   [green]✓[/green] Auth successful")
    except typer.Exit:
        console.print("   [red]✗[/red] Auth failed")
        raise

    # 3. KV engine / KV 엔진 확인
    console.print(f"\n3. KV engine: {settings.kv_mount}/")
    try:
        client.kv_list(settings.kv_mount, "")
        console.print("   [green]✓[/green] Accessible")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")

    console.print("\n[green]✓[/green] Test complete")


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions / 헬퍼 함수
# ═══════════════════════════════════════════════════════════════════════════════


def _check_root(command: str) -> None:
    """Check root privilege / root 권한 확인."""
    if os.geteuid() != 0:
        console.print(f"[red]✗[/red] Root privilege required.")
        console.print(f"  Run: sudo vaultctl {command}")
        raise typer.Exit(1)


def _write_config_file(
    path: Path,
    vault_addr: str,
    vault_token: Optional[str],
    role_id: Optional[str] = None,
    secret_id: Optional[str] = None,
) -> None:
    """Create config file / 환경 파일 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    
    content = f"""# vaultctl configuration
# Generated by vaultctl setup init

VAULT_ADDR={vault_addr}
"""
    if vault_token:
        content += f"VAULT_TOKEN={vault_token}\n"
    if role_id:
        content += f"VAULT_ROLE_ID={role_id}\n"
    if secret_id:
        content += f"VAULT_SECRET_ID={secret_id}\n"
    
    path.write_text(content)
    path.chmod(0o600)
    console.print(f"[green]✓[/green] Config file created: {path}")


def _setup_systemd_timer() -> None:
    """Enable systemd timer / systemd 타이머 활성화."""
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "vaultctl-renew.timer"], check=True)
        subprocess.run(["systemctl", "start", "vaultctl-renew.timer"], check=True)
        console.print("[green]✓[/green] systemd timer enabled")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] systemd setup failed: {e}")
        raise typer.Exit(1)


def _disable_systemd_timer() -> None:
    """Disable systemd timer / systemd 타이머 비활성화."""
    try:
        subprocess.run(["systemctl", "stop", "vaultctl-renew.timer"], check=False)
        subprocess.run(["systemctl", "disable", "vaultctl-renew.timer"], check=False)
        console.print("[green]✓[/green] systemd timer disabled")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] systemd setup failed: {e}")


def _show_systemd_status() -> None:
    """Show systemd status / systemd 상태 출력."""
    console.print("[bold]systemd Timer Status[/bold]\n")

    result = subprocess.run(
        ["systemctl", "is-active", "vaultctl-renew.timer"],
        capture_output=True,
        text=True,
    )
    is_active = result.stdout.strip() == "active"

    result = subprocess.run(
        ["systemctl", "is-enabled", "vaultctl-renew.timer"],
        capture_output=True,
        text=True,
    )
    is_enabled = result.stdout.strip() == "enabled"

    status_icon = "[green]●[/green]" if is_active else "[red]○[/red]"
    console.print(f"  Timer: {status_icon} {'active' if is_active else 'inactive'}")
    console.print(f"  Auto-start: {'yes' if is_enabled else 'no'}")

    if is_active:
        result = subprocess.run(
            ["systemctl", "show", "vaultctl-renew.timer", "--property=NextElapseUSecRealtime"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            next_run = result.stdout.strip().split("=")[1] if "=" in result.stdout else "unknown"
            console.print(f"  Next run: {next_run}")

    result = subprocess.run(
        ["systemctl", "show", "vaultctl-renew.service", "--property=ActiveState,Result"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            if "Result=" in line:
                last_result = line.split("=")[1]
                result_icon = "[green]✓[/green]" if last_result == "success" else "[yellow]![/yellow]"
                console.print(f"  Last result: {result_icon} {last_result}")


# ═══════════════════════════════════════════════════════════════════════════════
# APT Server Helper Functions / APT 서버 헬퍼 함수
# ═══════════════════════════════════════════════════════════════════════════════

APT_BASE = Path("/var/www/apt")
APT_REPO = APT_BASE / "repo"
APT_GPG_HOME = APT_BASE / ".gnupg"
APT_CONFIG_FILE = APT_BASE / ".config"


def _load_apt_config() -> dict:
    """Load existing APT config / 기존 APT 설정 로드."""
    if not APT_CONFIG_FILE.exists():
        return {}
    
    config = {}
    for line in APT_CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"')
    return config


def _save_apt_config(config: dict) -> None:
    """Save APT config / APT 설정 저장."""
    APT_BASE.mkdir(parents=True, exist_ok=True)
    
    content = f"""# APT Repository Configuration
# Generated by vaultctl setup apt-server

DOMAIN={config.get('DOMAIN', '')}
GPG_EMAIL={config.get('GPG_EMAIL', '')}
GPG_NAME={config.get('GPG_NAME', '')}
GPG_KEY_ID={config.get('GPG_KEY_ID', '')}
REPO_NAME={config.get('REPO_NAME', '')}
REPO_LABEL={config.get('REPO_LABEL', '')}
REPO_CODENAME={config.get('REPO_CODENAME', '')}
REPO_ARCH={config.get('REPO_ARCH', '')}
ENABLE_AUTH={config.get('ENABLE_AUTH', 'false')}
AUTH_USER={config.get('AUTH_USER', '')}
AUTH_PASS={config.get('AUTH_PASS', '')}
WEB_SERVER={config.get('WEB_SERVER', '')}
LISTEN_PORT={config.get('LISTEN_PORT', '8080')}
"""
    APT_CONFIG_FILE.write_text(content)
    APT_CONFIG_FILE.chmod(0o600)


def _get_gpg_key_id() -> Optional[str]:
    """Get GPG key ID using colon format / colon 형식으로 GPG 키 ID 추출."""
    result = subprocess.run(
        ["gpg", "--list-keys", "--with-colons"],
        capture_output=True,
        text=True,
    )
    
    for line in result.stdout.splitlines():
        if line.startswith("pub:"):
            # Format: pub:u:4096:1:KEYID:...
            parts = line.split(":")
            if len(parts) > 4:
                return parts[4][-8:]  # Last 8 chars of key ID
    
    return None


def _apt_full_install(config: dict, web_server: str) -> None:
    """Full APT server installation / 전체 APT 서버 설치."""
    console.print("\n[bold]Installing packages...[/bold]")
    
    # Install packages / 패키지 설치
    packages = ["reprepro", "gnupg", "gnupg-agent", "apache2-utils", "curl"]
    if web_server == "caddy":
        _install_caddy()
    else:
        packages.append("nginx")
        packages.append("libnginx-mod-http-fancyindex")
    
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    subprocess.run(["apt-get", "install", "-y", "-qq"] + packages, check=True)
    console.print("[green]✓[/green] Packages installed")
    
    # Create directories / 디렉토리 생성
    console.print("\n[bold]Creating directories...[/bold]")
    APT_REPO.mkdir(parents=True, exist_ok=True)
    (APT_REPO / "conf").mkdir(exist_ok=True)
    (APT_REPO / "db").mkdir(exist_ok=True)
    (APT_REPO / "dists").mkdir(exist_ok=True)
    (APT_REPO / "pool").mkdir(exist_ok=True)
    APT_GPG_HOME.mkdir(parents=True, exist_ok=True)
    APT_GPG_HOME.chmod(0o700)
    console.print("[green]✓[/green] Directories created")
    
    # Setup GPG / GPG 설정
    _setup_apt_gpg(config)
    
    # Save config / 설정 저장
    _save_apt_config(config)
    
    # Setup reprepro / reprepro 설정
    _setup_reprepro(config)
    
    # Setup auth / 인증 설정
    _setup_apt_auth(config)
    
    # Setup web server / 웹 서버 설정
    if web_server == "caddy":
        _setup_caddy(config)
    else:
        _setup_nginx(config)
    
    # Create client scripts / 클라이언트 스크립트 생성
    _create_client_files(config)


def _apt_reconfigure(config: dict, web_server: str) -> None:
    """Reconfigure APT server / APT 서버 재설정."""
    console.print("\n[bold]Reconfiguring...[/bold]")
    
    # Load existing GPG key ID / 기존 GPG 키 ID 로드
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    gpg_key_id = _get_gpg_key_id()
    
    if not gpg_key_id:
        console.print("[red]✗[/red] GPG key not found. Run full setup.")
        raise typer.Exit(1)
    
    config["GPG_KEY_ID"] = gpg_key_id
    console.print(f"[green]✓[/green] GPG Key ID: {gpg_key_id}")
    
    # Save config / 설정 저장
    _save_apt_config(config)
    
    # Update reprepro / reprepro 업데이트
    _setup_reprepro(config)
    
    # Update auth / 인증 업데이트
    _setup_apt_auth(config)
    
    # Update web server / 웹 서버 업데이트
    if web_server == "caddy":
        _setup_caddy(config)
    else:
        _setup_nginx(config)
    
    # Update client files / 클라이언트 파일 업데이트
    _create_client_files(config)


def _install_caddy() -> None:
    """Install Caddy web server / Caddy 웹서버 설치."""
    if shutil.which("caddy"):
        console.print("[green]✓[/green] Caddy already installed")
        return
    
    console.print("[dim]Installing Caddy...[/dim]")
    subprocess.run([
        "bash", "-c",
        "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | "
        "gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    ], check=True)
    subprocess.run([
        "bash", "-c",
        "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | "
        "tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null"
    ], check=True)
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    subprocess.run(["apt-get", "install", "-y", "-qq", "caddy"], check=True)


def _setup_apt_gpg(config: dict) -> None:
    """Setup GPG key for APT signing / APT 서명용 GPG 키 설정."""
    console.print("\n[bold]Setting up GPG key...[/bold]")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    # Check existing key / 기존 키 확인
    result = subprocess.run(
        ["gpg", "--list-keys", config["GPG_EMAIL"]],
        capture_output=True,
    )
    
    if result.returncode == 0:
        console.print(f"[green]✓[/green] Existing GPG key found: {config['GPG_EMAIL']}")
    else:
        # Generate new key / 새 키 생성
        console.print("[dim]Generating new GPG key (this may take a while)...[/dim]")
        
        batch_content = f"""%echo Generating APT signing key
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: {config["GPG_NAME"]}
Name-Email: {config["GPG_EMAIL"]}
Expire-Date: 0
%no-protection
%commit
%echo Done
"""
        batch_file = Path("/tmp/gpg-batch")
        batch_file.write_text(batch_content)
        
        subprocess.run(["gpg", "--batch", "--gen-key", str(batch_file)], check=True)
        batch_file.unlink()
        console.print("[green]✓[/green] GPG key generated")
    
    # Get key ID using helper / 헬퍼 함수로 키 ID 추출
    gpg_key_id = _get_gpg_key_id()
    
    if not gpg_key_id:
        console.print("[red]✗[/red] Failed to get GPG key ID")
        raise typer.Exit(1)
    
    config["GPG_KEY_ID"] = gpg_key_id
    console.print(f"  Key ID: {gpg_key_id}")
    
    # Export public key / 공개키 내보내기
    subprocess.run(
        ["gpg", "--armor", "--export"],
        stdout=open(APT_REPO / "key.gpg", "w"),
        check=True,
    )
    subprocess.run(
        ["gpg", "--export"],
        stdout=open(APT_REPO / "key", "wb"),
        check=True,
    )
    console.print("[green]✓[/green] Public key exported")


def _setup_reprepro(config: dict) -> None:
    """Setup reprepro / reprepro 설정."""
    console.print("\n[bold]Setting up reprepro...[/bold]")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    # distributions file
    distributions = f"""Origin: {config["REPO_NAME"]}
Label: {config["REPO_LABEL"]}
Codename: {config["REPO_CODENAME"]}
Architectures: {config["REPO_ARCH"]}
Components: main
Description: {config["REPO_LABEL"]}
SignWith: {config.get("GPG_KEY_ID", "default")}
"""
    (APT_REPO / "conf" / "distributions").write_text(distributions)
    
    # options file
    options = f"""verbose
basedir {APT_REPO}
gnupghome {APT_GPG_HOME}
ask-passphrase
"""
    (APT_REPO / "conf" / "options").write_text(options)
    
    # Initialize repository / 저장소 초기화
    subprocess.run(["reprepro", "-b", str(APT_REPO), "export"], check=False)
    console.print("[green]✓[/green] reprepro configured")


def _setup_apt_auth(config: dict) -> None:
    """Setup authentication / 인증 설정."""
    console.print("\n[bold]Setting up authentication...[/bold]")
    
    htpasswd_file = APT_BASE / ".htpasswd"
    credentials_file = APT_BASE / ".credentials"
    
    if config["ENABLE_AUTH"] != "true":
        console.print("[dim]Authentication disabled (public repository)[/dim]")
        htpasswd_file.unlink(missing_ok=True)
        credentials_file.unlink(missing_ok=True)
        return
    
    # Create htpasswd / htpasswd 생성
    subprocess.run([
        "htpasswd", "-bc", str(htpasswd_file),
        config["AUTH_USER"], config["AUTH_PASS"]
    ], check=True, capture_output=True)
    htpasswd_file.chmod(0o600)
    
    # Save credentials / 인증 정보 저장
    credentials_file.write_text(f"""# APT Repository Credentials
USER={config['AUTH_USER']}
PASS={config['AUTH_PASS']}
URL=https://{config['DOMAIN']}
""")
    credentials_file.chmod(0o600)
    
    console.print("[green]✓[/green] Authentication configured")
    console.print(f"\n[yellow]┌─────────────────────────────────────────────────┐[/yellow]")
    console.print(f"[yellow]│  Credentials (save securely!)                   │[/yellow]")
    console.print(f"[yellow]│  Username: {config['AUTH_USER']:<36}│[/yellow]")
    console.print(f"[yellow]│  Password: {config['AUTH_PASS']:<36}│[/yellow]")
    console.print(f"[yellow]└─────────────────────────────────────────────────┘[/yellow]")


def _setup_caddy(config: dict) -> None:
    """Setup Caddy web server / Caddy 웹서버 설정."""
    console.print("\n[bold]Setting up Caddy...[/bold]")
    
    enable_auth = config["ENABLE_AUTH"] == "true"
    caddy_hash = None
    
    if enable_auth:
        # Generate Caddy password hash / Caddy 비밀번호 해시 생성
        result = subprocess.run(
            ["caddy", "hash-password", "--plaintext", config["AUTH_PASS"]],
            capture_output=True,
            text=True,
        )
        caddy_hash = result.stdout.strip()
    
    # Render Caddyfile
    if enable_auth:
        caddyfile = f"""{config["DOMAIN"]} {{
    root * {APT_REPO}
    
    # Public files (GPG key, setup script)
    @public {{
        path /key.gpg /key /setup-client.sh /index.html
    }}
    handle @public {{
        file_server
    }}
    
    # Protected with authentication
    handle {{
        basicauth {{
            {config["AUTH_USER"]} {caddy_hash}
        }}
        file_server
    }}
    
    log {{
        output file /var/log/caddy/apt-access.log
    }}
}}
"""
    else:
        caddyfile = f"""{config["DOMAIN"]} {{
    root * {APT_REPO}
    file_server browse
    
    log {{
        output file /var/log/caddy/apt-access.log
    }}
}}
"""
    
    Path("/etc/caddy/Caddyfile").write_text(caddyfile)
    Path("/var/log/caddy").mkdir(parents=True, exist_ok=True)
    
    subprocess.run(["systemctl", "enable", "caddy"], check=True)
    subprocess.run(["systemctl", "restart", "caddy"], check=True)
    console.print("[green]✓[/green] Caddy configured (automatic HTTPS)")


def _setup_nginx(config: dict) -> None:
    """Setup Nginx web server / Nginx 웹서버 설정."""
    console.print("\n[bold]Setting up Nginx...[/bold]")
    
    # Remove default site / 기본 사이트 제거
    Path("/etc/nginx/sites-enabled/default").unlink(missing_ok=True)
    
    enable_auth = config["ENABLE_AUTH"] == "true"
    htpasswd_path = str(APT_BASE / ".htpasswd")
    listen_port = config["LISTEN_PORT"]
    domain = config["DOMAIN"]
    
    # Render nginx.conf
    if enable_auth:
        nginx_conf = f"""server {{
    listen {listen_port};
    server_name {domain};
    
    root {APT_REPO};
    
    # Public files (no authentication required)
    location ~ ^/(key\\.gpg|key|setup-client\\.sh|index\\.html|\\.fancyindex/.*)$ {{
        allow all;
    }}
    
    # Protected with authentication + fancyindex
    location / {{
        auth_basic "APT Repository";
        auth_basic_user_file {htpasswd_path};
        
        fancyindex on;
        fancyindex_exact_size off;
        fancyindex_localtime on;
        fancyindex_header "/.fancyindex/header.html";
        fancyindex_footer "/.fancyindex/footer.html";
        fancyindex_show_path off;
        fancyindex_name_length 255;
        fancyindex_time_format "%Y-%m-%d %H:%M";
    }}

    # Disable caching for repository metadata
    location /dists/ {{
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        auth_basic "APT Repository";
        auth_basic_user_file {htpasswd_path};
    }}
    
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}}
"""
    else:
        nginx_conf = f"""server {{
    listen {listen_port};
    server_name {domain};
    
    root {APT_REPO};
    
    # Directory listing with fancyindex (public)
    location / {{
        fancyindex on;
        fancyindex_exact_size off;
        fancyindex_localtime on;
        fancyindex_header "/.fancyindex/header.html";
        fancyindex_footer "/.fancyindex/footer.html";
        fancyindex_show_path off;
        fancyindex_name_length 255;
        fancyindex_time_format "%Y-%m-%d %H:%M";
    }}

    # Disable caching for repository metadata
    location /dists/ {{
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }}
    
    access_log /var/log/nginx/apt-access.log;
    error_log /var/log/nginx/apt-error.log;
}}
"""
    
    Path("/etc/nginx/sites-available/apt-repo").write_text(nginx_conf)
    Path("/etc/nginx/sites-enabled/apt-repo").unlink(missing_ok=True)
    Path("/etc/nginx/sites-enabled/apt-repo").symlink_to("/etc/nginx/sites-available/apt-repo")
    
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "enable", "nginx"], check=True)
    subprocess.run(["systemctl", "restart", "nginx"], check=True)
    console.print(f"[green]✓[/green] Nginx configured (port {listen_port})")


def _create_client_files(config: dict) -> None:
    """Create client setup files / 클라이언트 설정 파일 생성."""
    console.print("\n[bold]Creating client files...[/bold]")
    
    enable_auth = config["ENABLE_AUTH"] == "true"
    domain = config["DOMAIN"]
    repo_codename = config["REPO_CODENAME"]
    repo_arch = config["REPO_ARCH"]
    
    # setup-client.sh
    if enable_auth:
        client_script = f"""#!/bin/bash
# APT Repository Client Setup Script
set -e

DOMAIN="{domain}"
CODENAME="{repo_codename}"
AUTH_USER="${{1:?Usage: $0 USERNAME PASSWORD}}"
AUTH_PASS="${{2:?Usage: $0 USERNAME PASSWORD}}"

echo ""
echo "============================================================"
echo "  APT Repository Client Setup"
echo "============================================================"
echo ""

echo "[1/4] Adding GPG key..."
rm -f /usr/share/keyrings/internal-apt.gpg
curl -fsSL -u "$AUTH_USER:$AUTH_PASS" "https://$DOMAIN/key.gpg" | \\
    gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

echo "[2/4] Configuring authentication..."
mkdir -p /etc/apt/auth.conf.d
cat > /etc/apt/auth.conf.d/internal.conf << EOF
machine $DOMAIN
login $AUTH_USER
password $AUTH_PASS
EOF
chmod 600 /etc/apt/auth.conf.d/internal.conf

echo "[3/4] Adding APT source..."
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://$DOMAIN $CODENAME main" > \\
    /etc/apt/sources.list.d/internal.list

echo "[4/4] Updating package list..."
apt-get update -qq

echo ""
echo "Setup complete! Install with: sudo apt install vaultctl"
"""
    else:
        client_script = f"""#!/bin/bash
# APT Repository Client Setup Script
set -e

DOMAIN="{domain}"
CODENAME="{repo_codename}"

echo ""
echo "============================================================"
echo "  APT Repository Client Setup"
echo "============================================================"
echo ""

echo "[1/3] Adding GPG key..."
rm -f /usr/share/keyrings/internal-apt.gpg
curl -fsSL "https://$DOMAIN/key.gpg" | \\
    gpg --dearmor -o /usr/share/keyrings/internal-apt.gpg

echo "[2/3] Adding APT source..."
echo "deb [signed-by=/usr/share/keyrings/internal-apt.gpg] https://$DOMAIN $CODENAME main" > \\
    /etc/apt/sources.list.d/internal.list

echo "[3/3] Updating package list..."
apt-get update -qq

echo ""
echo "Setup complete! Install with: sudo apt install vaultctl"
"""
    (APT_REPO / "setup-client.sh").write_text(client_script)
    (APT_REPO / "setup-client.sh").chmod(0o755)
    
    # index.html
    badge_color = "#f59e0b" if enable_auth else "#10b981"
    badge_text = "Private" if enable_auth else "Public"
    auth_suffix = " -s -- USER PASSWORD" if enable_auth else ""
    manual_setup = f"""
        <div class="card">
            <h2 class="card-title">Manual Setup (vaultctl)</h2>
            <div class="code">sudo vaultctl setup apt-client https://{domain} -u USER -p PASSWORD</div>
        </div>
""" if enable_auth else ""
    
    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APT Repository - {domain}</title>
    <style>
        :root{{--bg:#fff;--fg:#0a0a0a;--muted:#f4f4f5;--muted-fg:#71717a;--border:#e4e4e7;--r:0.5rem}}
        *{{box-sizing:border-box;margin:0;padding:0}}
        body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5}}
        .container{{max-width:800px;margin:0 auto;padding:48px 24px}}
        .header{{margin-bottom:32px}}
        .header h1{{font-size:30px;font-weight:700;letter-spacing:-0.025em;margin-bottom:8px}}
        .header p{{color:var(--muted-fg);font-size:14px}}
        .badge{{display:inline-block;background:{badge_color};color:#fff;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-left:8px}}
        .card{{background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:24px;margin-bottom:24px}}
        .card-title{{font-size:18px;font-weight:600;margin-bottom:16px}}
        .btn{{display:inline-flex;padding:10px 16px;font-size:14px;font-weight:500;border-radius:var(--r);border:1px solid var(--border);background:var(--bg);color:var(--fg);text-decoration:none;margin-right:12px}}
        .btn:hover{{background:var(--muted)}}
        .code{{background:#1e1e2e;color:#cdd6f4;border-radius:var(--r);padding:16px;font-family:monospace;font-size:13px;overflow-x:auto;white-space:pre-wrap}}
        .info-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}}
        .info-item{{padding:16px;background:var(--muted);border-radius:var(--r)}}
        .info-item dt{{font-size:12px;color:var(--muted-fg);text-transform:uppercase;margin-bottom:4px}}
        .info-item dd{{font-size:14px;font-weight:500;font-family:monospace}}
        @media (max-width: 640px) {{
            .info-grid{{grid-template-columns:1fr}}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>APT Repository <span class="badge">{badge_text}</span></h1>
            <p>Internal package distribution</p>
        </div>
        <div class="info-grid">
            <dl class="info-item"><dt>Domain</dt><dd>{domain}</dd></dl>
            <dl class="info-item"><dt>Codename</dt><dd>{repo_codename}</dd></dl>
            <dl class="info-item"><dt>Arch</dt><dd>{repo_arch}</dd></dl>
        </div>
        <p style="margin-bottom:24px">
            <a href="/key.gpg" class="btn">GPG Key</a>
            <a href="/setup-client.sh" class="btn">Setup Script</a>
        </p>
        <div class="card">
            <h2 class="card-title">Quick Setup</h2>
            <div class="code">curl -fsSL https://{domain}/setup-client.sh | sudo bash{auth_suffix}</div>
        </div>
{manual_setup}
    </div>
</body>
</html>
"""
    (APT_REPO / "index.html").write_text(index_html)
    
    # fancyindex templates for nginx
    fancyindex_dir = APT_REPO / ".fancyindex"
    fancyindex_dir.mkdir(parents=True, exist_ok=True)
    
    header_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APT Repository - {domain}</title>
    <style>
        :root {{
            --bg: #ffffff;
            --fg: #0a0a0a;
            --muted: #f4f4f5;
            --muted-fg: #71717a;
            --border: #e4e4e7;
            --accent: #3b82f6;
            --r: 0.5rem;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--fg);
            line-height: 1.5;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 32px 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }}
        .header h1 {{
            font-size: 24px;
            font-weight: 600;
        }}
        .header .badge {{
            background: {badge_color};
            color: #fff;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .nav {{
            display: flex;
            gap: 12px;
        }}
        .nav a {{
            padding: 8px 14px;
            font-size: 13px;
            font-weight: 500;
            border-radius: var(--r);
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--fg);
            text-decoration: none;
        }}
        .nav a:hover {{
            background: var(--muted);
        }}
        .breadcrumb {{
            font-size: 13px;
            color: var(--muted-fg);
            margin-bottom: 16px;
        }}
        .breadcrumb a {{
            color: var(--accent);
            text-decoration: none;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            text-align: left;
            padding: 12px 16px;
            background: var(--muted);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            color: var(--muted-fg);
        }}
        td {{
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
        }}
        td a {{
            color: var(--fg);
            text-decoration: none;
            font-weight: 500;
        }}
        td a:hover {{
            color: var(--accent);
        }}
        tr:hover {{
            background: var(--muted);
        }}
        .size, .date {{
            color: var(--muted-fg);
            font-family: ui-monospace, monospace;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📦 APT Repository <span class="badge">{badge_text}</span></h1>
            <div class="nav">
                <a href="/">Home</a>
                <a href="/key.gpg">GPG Key</a>
                <a href="/setup-client.sh">Setup Script</a>
            </div>
        </div>
        <div class="breadcrumb">
"""
    (fancyindex_dir / "header.html").write_text(header_html)
    
    footer_html = """        </div>
        <div class="footer">
            <p>Powered by reprepro + GPG signing</p>
        </div>
    </div>
</body>
</html>
"""
    (fancyindex_dir / "footer.html").write_text(footer_html)
    
    console.print("[green]✓[/green] fancyindex templates created")
    
    # Set ownership / 소유권 설정
    subprocess.run(["chown", "-R", "www-data:www-data", str(APT_REPO)], check=False)
    
    console.print("[green]✓[/green] Client files created")


def _apt_client_remove(domain: str) -> None:
    """Remove APT client configuration / APT 클라이언트 설정 제거."""
    console.print(f"[bold]Removing APT client for {domain}...[/bold]")
    
    Path("/etc/apt/sources.list.d/internal.list").unlink(missing_ok=True)
    Path("/etc/apt/auth.conf.d/internal.conf").unlink(missing_ok=True)
    Path("/usr/share/keyrings/internal-apt.gpg").unlink(missing_ok=True)
    
    subprocess.run(["apt-get", "update", "-qq"], check=False)
    
    console.print("[green]✓[/green] APT client configuration removed")


def _print_apt_summary(config: dict, web_server: str) -> None:
    """Print APT setup summary / APT 설정 요약 출력."""
    console.print("\n")
    console.print(Panel.fit(
        f"[bold green]APT Server Setup Complete![/bold green]\n\n"
        f"URL: https://{config['DOMAIN']}\n"
        f"Web Server: {web_server.upper()}\n"
        f"Codename: {config['REPO_CODENAME']}\n\n"
        f"[bold]Next Steps:[/bold]\n"
        f"1. Add packages: vaultctl repo add <package.deb>\n"
        f"2. Client setup:\n"
        + (f"   curl -fsSL https://{config['DOMAIN']}/setup-client.sh | sudo bash -s -- {config['AUTH_USER']} '<password>'"
           if config['ENABLE_AUTH'] == 'true' else
           f"   curl -fsSL https://{config['DOMAIN']}/setup-client.sh | sudo bash"),
        title="✓ Complete",
    ))
