"""토큰 관리 명령어."""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vaultctl.config import settings
from vaultctl.onepassword import get_vault_token_from_op, save_vault_token_to_op
from vaultctl.vault_client import VaultError
from vaultctl.utils import format_duration
from vaultctl.commands.auth import ensure_authenticated

app = typer.Typer(help="토큰 관리")
console = Console()


@app.command("info")
def token_info():
    """현재 토큰 상세 정보."""
    client = ensure_authenticated()

    try:
        result = client.token_lookup()
        data = result.get("data", {})

        table = Table(show_header=False, box=None)
        table.add_column("Key", style="dim")
        table.add_column("Value")

        table.add_row("Accessor", data.get("accessor", "-"))
        table.add_row("Display Name", data.get("display_name", "-"))
        table.add_row("Policies", ", ".join(data.get("policies", [])))
        table.add_row("Token Policies", ", ".join(data.get("token_policies", [])))

        ttl = data.get("ttl", 0)
        explicit_max_ttl = data.get("explicit_max_ttl", 0)

        if ttl == 0:
            table.add_row("TTL", "[green]무제한[/green]")
        else:
            table.add_row("TTL", format_duration(ttl))

        if explicit_max_ttl == 0:
            table.add_row("Max TTL", "[green]무제한[/green]")
        else:
            table.add_row("Max TTL", format_duration(explicit_max_ttl))

        table.add_row("Renewable", "[green]예[/green]" if data.get("renewable") else "[red]아니오[/red]")
        table.add_row("Orphan", "예" if data.get("orphan") else "아니오")
        table.add_row("Creation Time", str(data.get("creation_time", "-")))
        table.add_row("Expire Time", str(data.get("expire_time", "없음")))
        table.add_row("Issue Time", str(data.get("issue_time", "-")))
        table.add_row("Num Uses", str(data.get("num_uses", 0)))

        # 메타데이터
        meta = data.get("meta", {})
        if meta:
            table.add_row("", "")
            table.add_row("[bold]Metadata[/bold]", "")
            for k, v in meta.items():
                table.add_row(f"  {k}", str(v))

        console.print(Panel(table, title="토큰 상세 정보", border_style="blue"))

        # 갱신 권장 여부
        if 0 < ttl < settings.token_renew_threshold:
            console.print(
                f"\n[yellow]⚠[/yellow] TTL이 {format_duration(ttl)}밖에 남지 않았습니다."
            )
            if data.get("renewable"):
                console.print("  갱신: vaultctl token renew")
            else:
                console.print("  이 토큰은 갱신할 수 없습니다. 새 토큰을 생성하세요.")

    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(1)


@app.command("renew")
def token_renew(
    increment: Optional[int] = typer.Option(
        None,
        "--increment",
        "-i",
        help=f"갱신 시간 (초). 기본: {settings.token_renew_increment}",
    ),
    save_to_op: bool = typer.Option(
        False,
        "--save-to-1password",
        help="갱신된 토큰을 1Password에 저장 (불필요 - 동일 토큰)",
    ),
):
    """토큰 갱신.

    토큰의 TTL을 연장합니다. 토큰 자체는 변경되지 않으므로
    1Password에 다시 저장할 필요가 없습니다.
    """
    client = ensure_authenticated()

    # 현재 토큰 정보 확인
    try:
        lookup = client.token_lookup()
        data = lookup.get("data", {})

        if not data.get("renewable"):
            console.print("[red]✗[/red] 이 토큰은 갱신할 수 없습니다.")
            console.print("  새 토큰 생성: vaultctl token create")
            raise typer.Exit(1)

        ttl = data.get("ttl", 0)
        if ttl == 0:
            console.print("[yellow]![/yellow] TTL이 무제한인 토큰입니다. 갱신이 필요 없습니다.")
            return

        old_ttl = format_duration(ttl)

    except VaultError as e:
        console.print(f"[red]✗[/red] 토큰 조회 실패: {e.message}")
        raise typer.Exit(1)

    # 갱신
    try:
        inc = increment or settings.token_renew_increment
        result = client.token_renew(increment=inc)
        auth = result.get("auth", {})
        new_ttl = auth.get("lease_duration", 0)

        console.print(f"[green]✓[/green] 토큰 갱신 완료")
        console.print(f"  이전 TTL: {old_ttl}")
        console.print(f"  새 TTL:   {format_duration(new_ttl)}")

    except VaultError as e:
        console.print(f"[red]✗[/red] 갱신 실패: {e.message}")
        raise typer.Exit(1)


@app.command("create")
def token_create(
    policies: list[str] = typer.Option(
        ["admin"],
        "--policy",
        "-p",
        help="적용할 정책 (여러 개 가능)",
    ),
    ttl: Optional[str] = typer.Option(
        None,
        "--ttl",
        "-t",
        help="TTL (예: 24h, 7d, 0=무제한)",
    ),
    display_name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="토큰 표시 이름",
    ),
    save_to_op: bool = typer.Option(
        False,
        "--save-to-1password",
        help="생성된 토큰을 1Password에 저장",
    ),
):
    """새 토큰 생성."""
    client = ensure_authenticated()

    try:
        result = client.token_create(
            policies=policies,
            ttl=ttl,
            display_name=display_name,
        )
        auth = result.get("auth", {})
        new_token = auth.get("client_token")

        console.print("[green]✓[/green] 토큰 생성 완료")
        console.print("")
        console.print("━" * 60)
        console.print(f"[bold]토큰:[/bold] {new_token}")
        console.print("━" * 60)
        console.print("")
        console.print(f"  Policies: {', '.join(auth.get('policies', []))}")
        console.print(f"  TTL: {format_duration(auth.get('lease_duration', 0))}")
        console.print(f"  Renewable: {'예' if auth.get('renewable') else '아니오'}")

        if save_to_op:
            console.print("")
            console.print("[dim]1Password에 저장 중...[/dim]")
            if save_vault_token_to_op(new_token):
                console.print("[green]✓[/green] 1Password에 저장 완료")
            else:
                console.print("[red]✗[/red] 1Password 저장 실패. 수동으로 저장하세요.")
        else:
            console.print("")
            console.print("[yellow]⚠[/yellow] 이 토큰을 안전한 곳에 저장하세요!")
            console.print("  1Password 저장: vaultctl token create --save-to-1password")

    except VaultError as e:
        console.print(f"[red]✗[/red] 생성 실패: {e.message}")
        raise typer.Exit(1)


@app.command("check")
def token_check():
    """토큰 상태 확인 및 갱신 필요 여부 체크.

    cron이나 systemd timer에서 사용할 수 있습니다.
    갱신이 필요하면 exit code 1을 반환합니다.
    """
    client = ensure_authenticated()

    try:
        result = client.token_lookup()
        data = result.get("data", {})

        ttl = data.get("ttl", 0)
        renewable = data.get("renewable", False)

        # TTL 무제한
        if ttl == 0:
            console.print("[green]✓[/green] TTL 무제한")
            raise typer.Exit(0)

        # 갱신 필요 여부 확인
        if ttl < settings.token_renew_threshold:
            console.print(
                f"[yellow]![/yellow] 갱신 필요: {format_duration(ttl)} 남음"
            )
            if renewable:
                console.print("  실행: vaultctl token renew")
            else:
                console.print("  (갱신 불가 토큰)")
            raise typer.Exit(1)
        else:
            console.print(f"[green]✓[/green] 정상: {format_duration(ttl)} 남음")
            raise typer.Exit(0)

    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(2)


@app.command("auto-renew")
def token_auto_renew(
    threshold: int = typer.Option(
        settings.token_renew_threshold,
        "--threshold",
        "-t",
        help="갱신 임계값 (초)",
    ),
    increment: int = typer.Option(
        settings.token_renew_increment,
        "--increment",
        "-i",
        help="갱신 증가량 (초)",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="출력 최소화"),
):
    """토큰 자동 갱신 (cron/systemd timer용).

    TTL이 threshold 이하일 때만 갱신합니다.
    갱신 불필요 시 아무것도 하지 않습니다.

    예시 cron (매시간):
        0 * * * * vaultctl token auto-renew --quiet

    예시 systemd timer:
        [Timer]
        OnCalendar=hourly
        Persistent=true
    """
    client = ensure_authenticated()

    try:
        result = client.token_lookup()
        data = result.get("data", {})

        ttl = data.get("ttl", 0)
        renewable = data.get("renewable", False)

        # TTL 무제한이면 갱신 불필요
        if ttl == 0:
            if not quiet:
                console.print("[dim]TTL 무제한 - 갱신 불필요[/dim]")
            raise typer.Exit(0)

        # 갱신 불가 토큰
        if not renewable:
            if not quiet:
                console.print("[yellow]![/yellow] 갱신 불가 토큰")
            raise typer.Exit(0)

        # 임계값 이상이면 갱신 불필요
        if ttl >= threshold:
            if not quiet:
                console.print(f"[dim]TTL {format_duration(ttl)} - 갱신 불필요[/dim]")
            raise typer.Exit(0)

        # 갱신 실행
        result = client.token_renew(increment=increment)
        auth = result.get("auth", {})
        new_ttl = auth.get("lease_duration", 0)

        if not quiet:
            console.print(
                f"[green]✓[/green] 갱신 완료: {format_duration(ttl)} → {format_duration(new_ttl)}"
            )

    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(1)
