"""vaultctl - Simple Vault CLI for LXC environments.

Usage (User):
    vaultctl init              # 초기 설정 (한 번만)
    vaultctl env <lxc-name>    # .env 파일 생성
    vaultctl status            # 연결/인증 상태 확인
    
    vaultctl run <n> -- cmd    # 환경변수 주입 실행
    vaultctl sh <n>            # 셸 export 생성
    vaultctl watch <n> -- cmd  # 비밀 변경 감지 & 재시작
    
Usage (Admin):
    vaultctl admin ...         # 관리자 명령어
"""
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from vaultctl import __version__
from vaultctl.commands import admin, extended
from vaultctl.config import settings
from vaultctl.utils import format_duration, write_env_file
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(
    name="vaultctl",
    help="Simple Vault CLI for LXC environments / LXC 환경을 위한 간단한 Vault CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# Admin sub-command
app.add_typer(admin.app, name="admin", help="Administrator commands / 관리자 명령어")

# Extended commands (user-facing)
app.command("run")(extended.run_command)
app.command("sh")(extended.shell_export)
app.command("scan")(extended.scan_secrets)
app.command("redact")(extended.redact_secrets)
app.command("watch")(extended.watch_and_restart)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client / 인증된 클라이언트 반환."""
    client = VaultClient()
    
    # 1. Try cached token
    if settings.token_cache_file.exists():
        try:
            token = settings.token_cache_file.read_text().strip()
            if token:
                client = VaultClient(token=token)
                if client.is_authenticated():
                    return client
        except PermissionError:
            pass
    
    # 2. Try config token
    if settings.vault_token:
        client = VaultClient(token=settings.vault_token)
        if client.is_authenticated():
            return client
    
    # 3. Try AppRole auto-login
    if settings.has_approle_credentials():
        try:
            result = client.approle_login(
                settings.approle_role_id,
                settings.approle_secret_id,
                settings.approle_mount,
            )
            token = result.get("auth", {}).get("client_token")
            if token:
                # Cache the token
                try:
                    settings.ensure_dirs()
                    settings.token_cache_file.write_text(token)
                    settings.token_cache_file.chmod(0o600)
                except PermissionError:
                    pass
                return VaultClient(token=token)
        except VaultError:
            pass
    
    console.print("[red]✗[/red] 인증이 필요합니다.")
    console.print("  실행: vaultctl init")
    raise typer.Exit(1)


def _get_secret_path(name: str) -> str:
    """Get KV secret path / 시크릿 경로 생성."""
    return f"{settings.kv_lxc_path}/{name}"


# ═══════════════════════════════════════════════════════════════════════════════
# Main Commands (for regular users)
# ═══════════════════════════════════════════════════════════════════════════════


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="버전 출력"),
):
    """Simple Vault CLI for LXC environments.
    
    \b
    Quick Start:
        vaultctl init              # 초기 설정 (한 번만)
        vaultctl env lxc-161       # .env 파일 생성
        docker compose up -d       # 실행
    
    \b
    Advanced:
        vaultctl run lxc-161 -- node app.js   # 환경변수 주입 실행
        vaultctl watch lxc-161 -- docker compose up  # 자동 재시작
        eval "$(vaultctl sh lxc-161)"         # 셸 환경변수 로드
    
    \b
    Administrator:
        vaultctl admin list        # 시크릿 목록
        vaultctl admin put lxc-161 DB_HOST=localhost
        vaultctl admin setup vault # Vault 초기 설정
    """
    if version:
        console.print(f"vaultctl {__version__}")
        raise typer.Exit(0)


@app.command("init")
def init_command():
    """Initialize vaultctl (one-time setup) / 초기 설정 (한 번만).
    
    Configures Vault connection and AppRole authentication.
    설정이 ~/.config/vaultctl/에 저장됩니다.
    
    \b
    Examples:
        vaultctl init
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl 초기 설정[/bold blue]\n\n"
        "Vault 연결 및 인증을 설정합니다.\n"
        "이 설정은 한 번만 하면 됩니다.",
        title="🔐 Setup",
    ))
    console.print()
    
    # 1. Vault address
    default_addr = settings.vault_addr
    if default_addr == "https://vault.example.com":
        default_addr = ""
    
    vault_addr = Prompt.ask(
        "Vault 서버 주소",
        default=default_addr or None,
    )
    
    if not vault_addr:
        console.print("[red]✗[/red] Vault 주소를 입력하세요.")
        raise typer.Exit(1)
    
    # Test connection
    console.print(f"\n[dim]연결 테스트: {vault_addr}[/dim]")
    client = VaultClient(addr=vault_addr)
    health = client.health()
    
    if not health.get("initialized"):
        console.print("[red]✗[/red] Vault 서버에 연결할 수 없습니다.")
        raise typer.Exit(1)
    
    if health.get("sealed"):
        console.print("[red]✗[/red] Vault 서버가 sealed 상태입니다.")
        raise typer.Exit(1)
    
    console.print("[green]✓[/green] 연결 성공")
    
    # 2. AppRole credentials
    console.print("\n[bold]AppRole 인증 정보[/bold]")
    console.print("[dim]관리자에게 Role ID와 Secret ID를 받으세요.[/dim]")
    
    role_id = Prompt.ask("Role ID")
    secret_id = Prompt.ask("Secret ID", password=True)
    
    if not role_id or not secret_id:
        console.print("[red]✗[/red] Role ID와 Secret ID를 모두 입력하세요.")
        raise typer.Exit(1)
    
    # 3. Test AppRole login
    console.print("\n[dim]인증 테스트...[/dim]")
    try:
        result = client.approle_login(role_id, secret_id, settings.approle_mount)
        token = result.get("auth", {}).get("client_token")
        
        if not token:
            console.print("[red]✗[/red] 인증 실패: 토큰을 받지 못했습니다.")
            raise typer.Exit(1)
        
        console.print("[green]✓[/green] 인증 성공")
        
        auth_data = result.get("auth", {})
        console.print(f"  Policies: {', '.join(auth_data.get('policies', []))}")
        ttl = auth_data.get("lease_duration", 0)
        console.print(f"  TTL: {format_duration(ttl)}")
        
    except VaultError as e:
        console.print(f"[red]✗[/red] 인증 실패: {e.message}")
        raise typer.Exit(1)
    
    # 4. Save configuration
    console.print("\n[dim]설정 저장 중...[/dim]")
    
    try:
        settings.ensure_dirs()
        
        # Save config (vault addr, role_id, secret_id)
        config_file = settings.config_dir / "config"
        config_file.write_text(f"""# vaultctl configuration
VAULT_ADDR={vault_addr}
VAULT_ROLE_ID={role_id}
VAULT_SECRET_ID={secret_id}
""")
        config_file.chmod(0o600)
        
        # Save token cache
        settings.token_cache_file.write_text(token)
        settings.token_cache_file.chmod(0o600)
        
        console.print(f"[green]✓[/green] 설정 저장: {settings.config_dir}/")
        
    except PermissionError as e:
        console.print(f"[yellow]![/yellow] 설정 저장 실패: {e}")
        console.print("  토큰은 메모리에만 유지됩니다.")
    
    # 5. Done
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]설정 완료![/bold green]\n\n"
        "이제 다음 명령어를 사용할 수 있습니다:\n"
        "  vaultctl env <lxc-name>    # .env 파일 생성\n"
        "  vaultctl status            # 상태 확인\n"
        "  vaultctl run <n> -- cmd    # 환경변수 주입 실행",
        title="✓ Complete",
    ))


@app.command("env")
def env_command(
    name: str = typer.Argument(..., help="시크릿 이름 (예: lxc-161)"),
    output: Path = typer.Option(Path(".env"), "--output", "-o", help="출력 파일"),
    stdout: bool = typer.Option(False, "--stdout", help="stdout으로 출력"),
):
    """Generate .env file from Vault / Vault에서 .env 파일 생성.
    
    \b
    Examples:
        vaultctl env lxc-161              # .env 파일 생성
        vaultctl env lxc-161 -o prod.env  # 다른 파일명
        vaultctl env lxc-161 --stdout     # stdout 출력
        
        # docker compose와 함께 사용
        vaultctl env lxc-161 && docker compose up -d
    """
    client = _get_authenticated_client()
    
    try:
        data = client.kv_get(settings.kv_mount, _get_secret_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] 시크릿을 찾을 수 없습니다: {name}")
            console.print(f"  경로: {settings.kv_mount}/{_get_secret_path(name)}")
            console.print("\n  관리자에게 시크릿 등록을 요청하세요:")
            console.print(f"    vaultctl admin put {name} KEY=value ...")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] 시크릿이 비어있습니다: {name}")
        raise typer.Exit(1)

    if stdout:
        for key, value in sorted(data.items()):
            console.print(f"{key}={value}")
    else:
        write_env_file(str(output), data, header=f"Generated from Vault: {name}")
        console.print(f"[green]✓[/green] {output} ({len(data)}개 변수)")


@app.command("status")
def status_command():
    """Show connection and auth status / 연결 및 인증 상태 확인.
    
    \b
    Examples:
        vaultctl status
    """
    console.print("[bold]vaultctl 상태[/bold]\n")
    
    # 1. Config
    console.print("1. 설정")
    console.print(f"   Vault: {settings.vault_addr}")
    console.print(f"   KV 경로: {settings.kv_mount}/{settings.kv_lxc_path}/")
    
    if settings.config_dir.exists():
        console.print(f"   설정 디렉토리: [green]✓[/green] {settings.config_dir}")
    else:
        console.print(f"   설정 디렉토리: [yellow]![/yellow] 없음")
    
    # 2. Connection
    console.print("\n2. 연결")
    client = VaultClient()
    health = client.health()
    
    if health.get("initialized") and not health.get("sealed"):
        console.print("   [green]✓[/green] Vault 서버 연결됨")
    else:
        console.print("   [red]✗[/red] Vault 서버 연결 실패")
        raise typer.Exit(1)
    
    # 3. Authentication
    console.print("\n3. 인증")
    
    try:
        client = _get_authenticated_client()
        token_info = client.token_lookup()
        data = token_info.get("data", {})
        
        console.print("   [green]✓[/green] 인증됨")
        console.print(f"   Policies: {', '.join(data.get('policies', []))}")
        
        ttl = data.get("ttl", 0)
        if ttl == 0:
            console.print("   TTL: [green]무제한[/green]")
        else:
            remaining = format_duration(ttl)
            if ttl < settings.token_renew_threshold:
                console.print(f"   TTL: [yellow]{remaining}[/yellow] (갱신 권장)")
            else:
                console.print(f"   TTL: {remaining}")
                
    except typer.Exit:
        console.print("   [red]✗[/red] 인증 필요")
        console.print("   실행: vaultctl init")
        raise
    
    # 4. Secrets access test
    console.print("\n4. 시크릿 접근")
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_lxc_path)
        console.print(f"   [green]✓[/green] 접근 가능 ({len(items) if items else 0}개 시크릿)")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    console.print("\n[green]✓[/green] 모든 상태 정상")


@app.command("config")
def config_command():
    """Show current configuration / 현재 설정 출력.
    
    \b
    Examples:
        vaultctl config
    """
    table = Table(title="현재 설정", show_header=True, header_style="bold cyan")
    table.add_column("설정", style="green")
    table.add_column("값", style="white")

    configs = [
        ("Vault 주소", settings.vault_addr),
        ("KV 마운트", settings.kv_mount),
        ("시크릿 경로", settings.kv_lxc_path),
        ("AppRole Role ID", settings.approle_role_id[:8] + "..." if settings.approle_role_id else "-"),
        ("설정 디렉토리", str(settings.config_dir)),
        ("캐시 디렉토리", str(settings.cache_dir)),
    ]

    for name, value in configs:
        table.add_row(name, str(value) if value else "-")

    console.print(table)


if __name__ == "__main__":
    app()
