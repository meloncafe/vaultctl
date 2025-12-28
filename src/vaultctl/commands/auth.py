"""인증 관련 명령어."""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vaultctl.config import settings
from vaultctl.utils import format_duration
from vaultctl.vault_client import VaultClient, VaultError, get_client, set_token

app = typer.Typer(help="인증 관리")
console = Console()


@app.command("login")
def login(
    token: Optional[str] = typer.Option(
        None, "--token", "-t", help="Vault 토큰 직접 입력"
    ),
    approle: bool = typer.Option(
        False, "--approle", "-a", help="AppRole 인증 사용"
    ),
    role_id: Optional[str] = typer.Option(
        None, "--role-id", help="AppRole Role ID (또는 환경변수 VAULTCTL_APPROLE_ROLE_ID)"
    ),
    secret_id: Optional[str] = typer.Option(
        None, "--secret-id", help="AppRole Secret ID (또는 환경변수 VAULTCTL_APPROLE_SECRET_ID)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="기존 세션 무시하고 재인증"),
):
    """Vault 인증.
    
    토큰 직접 입력 또는 AppRole 인증을 사용합니다.
    
    Examples:
        vaultctl auth login --token hvs.xxx
        vaultctl auth login --approle --role-id xxx --secret-id yyy
        vaultctl auth login --approle  # 환경변수에서 role_id, secret_id 로드
    """
    # 이미 인증된 상태 확인
    if not force:
        client = get_client()
        if client.is_authenticated():
            console.print("[green]✓[/green] 이미 인증되어 있습니다.")
            _show_token_info(client)
            return

    vault_token = None

    # 1. 토큰 직접 입력
    if token:
        vault_token = token
        console.print("[dim]토큰이 직접 제공됨[/dim]")

    # 2. AppRole 인증
    elif approle or settings.has_approle_credentials():
        r_id = role_id or settings.approle_role_id
        s_id = secret_id or settings.approle_secret_id
        
        if not r_id or not s_id:
            console.print("[red]✗[/red] AppRole 자격 증명이 필요합니다.")
            console.print("  --role-id와 --secret-id를 제공하거나")
            console.print("  환경변수 VAULTCTL_APPROLE_ROLE_ID, VAULTCTL_APPROLE_SECRET_ID를 설정하세요.")
            raise typer.Exit(1)
        
        console.print("[dim]AppRole 인증 중...[/dim]")
        vault_token = _approle_login(r_id, s_id)
    
    # 3. 환경변수에서 토큰 확인
    elif settings.vault_token:
        vault_token = settings.vault_token
        console.print("[dim]환경변수에서 토큰 로드됨[/dim]")
    
    else:
        console.print("[red]✗[/red] 인증 방법을 지정해주세요.")
        console.print("  --token: 토큰 직접 입력")
        console.print("  --approle: AppRole 인증")
        raise typer.Exit(1)

    # 토큰 설정 및 검증
    set_token(vault_token)
    client = get_client()

    try:
        token_info = client.token_lookup()
        console.print("[green]✓[/green] Vault 인증 완료")
        _show_token_info(client, token_info)

        # 토큰 캐시 저장
        settings.ensure_dirs()
        settings.token_cache_file.write_text(vault_token)
        settings.token_cache_file.chmod(0o600)

    except VaultError as e:
        console.print(f"[red]✗[/red] 인증 실패: {e.message}")
        raise typer.Exit(1)


@app.command("logout")
def logout():
    """세션 종료 (캐시된 토큰 삭제)."""
    if settings.token_cache_file.exists():
        settings.token_cache_file.unlink()
    console.print("[green]✓[/green] 로그아웃 완료")


@app.command("status")
def status():
    """현재 인증 상태 확인."""
    client = get_client()

    # 서버 상태 확인
    health = client.health()

    if not health.get("initialized", False):
        console.print("[red]✗[/red] Vault 서버가 초기화되지 않았습니다.")
        raise typer.Exit(1)

    if health.get("sealed", True):
        console.print("[red]✗[/red] Vault 서버가 sealed 상태입니다.")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Vault 서버: {settings.vault_addr}")

    # 인증 상태 확인
    if client.is_authenticated():
        console.print("[green]✓[/green] 인증됨")
        _show_token_info(client)
    else:
        console.print("[yellow]![/yellow] 인증되지 않음")
        if settings.has_approle_credentials():
            console.print("  실행: vaultctl auth login --approle")
        else:
            console.print("  실행: vaultctl auth login --token <TOKEN>")


@app.command("whoami")
def whoami():
    """현재 토큰 정보 조회."""
    client = get_client()

    try:
        _show_token_info(client)
    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(1)


def _approle_login(role_id: str, secret_id: str) -> str:
    """AppRole 로그인하여 토큰 반환."""
    client = VaultClient(addr=settings.vault_addr)
    
    try:
        result = client.approle_login(
            role_id=role_id,
            secret_id=secret_id,
            mount=settings.approle_mount,
        )
        
        auth_data = result.get("auth", {})
        token = auth_data.get("client_token")
        
        if not token:
            raise VaultError("AppRole 로그인 응답에 토큰이 없습니다.")
        
        return token
    
    except VaultError:
        raise
    finally:
        client.close()


def _show_token_info(client: VaultClient, token_info: Optional[dict] = None) -> None:
    """토큰 정보 출력."""
    if token_info is None:
        token_info = client.token_lookup()

    data = token_info.get("data", {})

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="dim")
    table.add_column("Value")

    table.add_row("Display Name", data.get("display_name", "-"))
    table.add_row("Policies", ", ".join(data.get("policies", [])))

    ttl = data.get("ttl", 0)
    if ttl == 0:
        table.add_row("TTL", "[green]무제한[/green]")
    else:
        remaining = format_duration(ttl)
        if ttl < settings.token_renew_threshold:
            table.add_row("TTL", f"[yellow]{remaining}[/yellow] (갱신 권장)")
        else:
            table.add_row("TTL", remaining)

    table.add_row("Renewable", "예" if data.get("renewable", False) else "아니오")
    table.add_row("Creation Time", data.get("creation_time", "-"))

    console.print(Panel(table, title="토큰 정보", border_style="blue"))


def ensure_authenticated() -> VaultClient:
    """인증된 클라이언트 반환. 미인증 시 AppRole 로그인 시도."""
    client = get_client()

    # 1. 캐시된 토큰 로드 시도
    if not client.token and settings.token_cache_file.exists():
        cached_token = settings.token_cache_file.read_text().strip()
        if cached_token:
            set_token(cached_token)
            client = get_client()

    # 2. 현재 토큰이 유효한지 확인
    if client.is_authenticated():
        return client

    # 3. AppRole 자격 증명이 있으면 자동 로그인
    if settings.has_approle_credentials():
        console.print("[dim]토큰 만료됨. AppRole로 재인증 중...[/dim]")
        try:
            vault_token = _approle_login(
                settings.approle_role_id,
                settings.approle_secret_id,
            )
            set_token(vault_token)
            client = get_client()
            
            if client.is_authenticated():
                # 캐시 저장
                settings.ensure_dirs()
                settings.token_cache_file.write_text(vault_token)
                settings.token_cache_file.chmod(0o600)
                console.print("[green]✓[/green] AppRole 재인증 성공")
                return client
        except VaultError as e:
            console.print(f"[red]✗[/red] AppRole 재인증 실패: {e.message}")

    console.print("[red]✗[/red] 인증이 필요합니다.")
    if settings.has_approle_credentials():
        console.print("  AppRole 자격 증명이 유효하지 않습니다.")
    console.print("  실행: vaultctl auth login")
    raise typer.Exit(1)
