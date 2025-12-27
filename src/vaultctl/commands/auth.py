"""인증 관련 명령어."""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import settings
from ..onepassword import get_vault_token_from_op, is_op_installed, is_op_signed_in
from ..vault_client import VaultClient, VaultError, get_client, set_token
from ..utils import format_duration

app = typer.Typer(help="인증 관리")
console = Console()


@app.command("login")
def login(
    token: Optional[str] = typer.Option(
        None, "--token", "-t", help="Vault 토큰 (생략 시 1Password에서 로드)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="기존 세션 무시하고 재인증"),
):
    """Vault 인증 (1Password 연동)."""
    # 이미 인증된 상태 확인
    if not force:
        client = get_client()
        if client.is_authenticated():
            console.print("[green]✓[/green] 이미 인증되어 있습니다.")
            _show_token_info(client)
            return

    # 토큰 가져오기
    if token:
        vault_token = token
        console.print("[dim]토큰이 직접 제공됨[/dim]")
    else:
        # 1Password에서 토큰 로드
        if not is_op_installed():
            console.print("[red]✗[/red] 1Password CLI가 설치되지 않았습니다.")
            console.print("  설치: brew install 1password-cli")
            raise typer.Exit(1)

        if not is_op_signed_in():
            console.print("[yellow]![/yellow] 1Password 로그인이 필요합니다.")
            console.print("  실행: eval $(op signin)")
            raise typer.Exit(1)

        console.print("[dim]1Password에서 토큰 로드 중...[/dim]")
        vault_token = get_vault_token_from_op()

        if not vault_token:
            console.print(
                f"[red]✗[/red] 1Password에서 토큰을 찾을 수 없습니다.\n"
                f"  Vault: {settings.op_vault}\n"
                f"  Item: {settings.op_item}\n"
                f"  Field: {settings.op_field}"
            )
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
        console.print("  실행: vaultctl auth login")


@app.command("whoami")
def whoami():
    """현재 토큰 정보 조회."""
    client = get_client()

    try:
        _show_token_info(client)
    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(1)


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
    """인증된 클라이언트 반환. 미인증 시 로그인 시도."""
    client = get_client()

    # 캐시된 토큰 로드 시도
    if not client.token and settings.token_cache_file.exists():
        cached_token = settings.token_cache_file.read_text().strip()
        if cached_token:
            set_token(cached_token)
            client = get_client()

    if client.is_authenticated():
        return client

    # 1Password에서 토큰 로드 시도
    if is_op_installed() and is_op_signed_in():
        vault_token = get_vault_token_from_op()
        if vault_token:
            set_token(vault_token)
            client = get_client()
            if client.is_authenticated():
                # 캐시 저장
                settings.ensure_dirs()
                settings.token_cache_file.write_text(vault_token)
                settings.token_cache_file.chmod(0o600)
                return client

    console.print("[red]✗[/red] 인증이 필요합니다.")
    console.print("  실행: vaultctl auth login")
    raise typer.Exit(1)
