"""설정 관련 명령어."""

import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from vaultctl.config import settings
from vaultctl.onepassword import get_vault_token_from_op, is_op_installed, is_op_signed_in
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(help="초기 설정 및 systemd 관리")
console = Console()


@app.command("init")
def init_setup(
    vault_addr: Optional[str] = typer.Option(
        None,
        "--vault-addr",
        "-a",
        help="Vault 서버 주소",
    ),
    use_1password: bool = typer.Option(
        True,
        "--1password/--no-1password",
        help="1Password 연동 사용",
    ),
    setup_timer: bool = typer.Option(
        True,
        "--timer/--no-timer",
        help="systemd 타이머 설정",
    ),
):
    """vaultctl 초기 설정 마법사.

    대화형으로 Vault 연결, 토큰, systemd 타이머를 설정합니다.
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl 초기 설정[/bold blue]\n\n"
        "이 마법사가 다음을 설정합니다:\n"
        "• Vault 서버 연결\n"
        "• 인증 토큰 (1Password 또는 직접 입력)\n"
        "• systemd 자동 갱신 타이머",
        title="🔐 Setup Wizard",
    ))
    console.print()

    # 1. Vault 주소 설정
    if not vault_addr:
        vault_addr = Prompt.ask(
            "Vault 서버 주소",
            default=settings.vault_addr,
        )

    # 연결 테스트
    console.print(f"\n[dim]Vault 서버 연결 테스트: {vault_addr}[/dim]")
    client = VaultClient(addr=vault_addr)
    health = client.health()

    if not health.get("initialized"):
        console.print("[red]✗[/red] Vault 서버에 연결할 수 없거나 초기화되지 않았습니다.")
        raise typer.Exit(1)

    if health.get("sealed"):
        console.print("[red]✗[/red] Vault 서버가 sealed 상태입니다.")
        raise typer.Exit(1)

    console.print("[green]✓[/green] Vault 서버 연결 성공")

    # 2. 토큰 설정
    vault_token = None

    if use_1password and is_op_installed():
        if is_op_signed_in():
            console.print("\n[dim]1Password에서 토큰 로드 시도...[/dim]")
            vault_token = get_vault_token_from_op()
            if vault_token:
                console.print("[green]✓[/green] 1Password에서 토큰 로드 성공")
        else:
            console.print("[yellow]![/yellow] 1Password 로그인이 필요합니다.")
            console.print("  실행: eval $(op signin)")

    if not vault_token:
        vault_token = Prompt.ask(
            "\nVault 토큰을 입력하세요",
            password=True,
        )

    # 토큰 검증
    client = VaultClient(addr=vault_addr, token=vault_token)
    try:
        token_info = client.token_lookup()
        console.print("[green]✓[/green] 토큰 검증 성공")

        data = token_info.get("data", {})
        console.print(f"  Policies: {', '.join(data.get('policies', []))}")
        ttl = data.get("ttl", 0)
        console.print(f"  TTL: {'무제한' if ttl == 0 else f'{ttl}초'}")
    except VaultError as e:
        console.print(f"[red]✗[/red] 토큰 검증 실패: {e.message}")
        raise typer.Exit(1)

    # 3. 환경 파일 생성
    env_file = Path("/etc/vaultctl/env")
    env_example = Path("/etc/vaultctl/env.example")

    if env_file.exists():
        if not Confirm.ask("\n/etc/vaultctl/env가 이미 존재합니다. 덮어쓰시겠습니까?"):
            console.print("[dim]환경 파일 유지[/dim]")
        else:
            _write_env_file(env_file, vault_addr, vault_token)
    else:
        if os.geteuid() == 0:
            _write_env_file(env_file, vault_addr, vault_token)
        else:
            console.print("\n[yellow]![/yellow] 환경 파일 생성에 root 권한이 필요합니다.")
            console.print("  수동 생성:")
            console.print(f"    sudo cp {env_example} {env_file}")
            console.print(f"    sudo chmod 600 {env_file}")
            console.print("    sudo nano /etc/vaultctl/env")

    # 4. systemd 타이머 설정
    if setup_timer:
        console.print("\n[bold]systemd 타이머 설정[/bold]")

        if os.geteuid() != 0:
            console.print("[yellow]![/yellow] root 권한이 필요합니다.")
            console.print("  수동 활성화:")
            console.print("    sudo systemctl enable --now vaultctl-renew.timer")
        else:
            if Confirm.ask("토큰 자동 갱신 타이머를 활성화하시겠습니까?"):
                _setup_systemd_timer()

    # 5. 완료
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]설정 완료![/bold green]\n\n"
        "다음 명령어를 사용해보세요:\n"
        "  vaultctl auth status    # 인증 상태 확인\n"
        "  vaultctl lxc list       # LXC 목록\n"
        "  vaultctl --help         # 전체 도움말",
        title="✓ Complete",
    ))


@app.command("systemd")
def systemd_setup(
    enable: bool = typer.Option(None, "--enable/--disable", help="타이머 활성화/비활성화"),
    status: bool = typer.Option(False, "--status", "-s", help="타이머 상태 확인"),
):
    """systemd 타이머 관리.

    토큰 자동 갱신 타이머를 활성화/비활성화하거나 상태를 확인합니다.
    """
    if status:
        _show_systemd_status()
        return

    if enable is None:
        # 현재 상태 표시 후 선택
        _show_systemd_status()
        enable = Confirm.ask("\n타이머를 활성화하시겠습니까?")

    if os.geteuid() != 0:
        console.print("[red]✗[/red] root 권한이 필요합니다.")
        if enable:
            console.print("  실행: sudo vaultctl setup systemd --enable")
        else:
            console.print("  실행: sudo vaultctl setup systemd --disable")
        raise typer.Exit(1)

    if enable:
        _setup_systemd_timer()
    else:
        _disable_systemd_timer()


@app.command("env")
def show_env(
    edit: bool = typer.Option(False, "--edit", "-e", help="환경 파일 편집"),
):
    """환경 설정 파일 관리."""
    env_file = Path("/etc/vaultctl/env")
    env_example = Path("/etc/vaultctl/env.example")

    if edit:
        if os.geteuid() != 0:
            console.print("[red]✗[/red] root 권한이 필요합니다.")
            console.print("  실행: sudo vaultctl setup env --edit")
            raise typer.Exit(1)

        if not env_file.exists() and env_example.exists():
            console.print("[dim]env.example에서 복사 중...[/dim]")
            import shutil
            shutil.copy(env_example, env_file)
            env_file.chmod(0o600)

        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(env_file)])
        return

    # 현재 설정 출력
    console.print("[bold]환경 파일 위치[/bold]")
    console.print(f"  설정 파일: {env_file} {'[green](존재)[/green]' if env_file.exists() else '[red](없음)[/red]'}")
    console.print(f"  예시 파일: {env_example} {'[green](존재)[/green]' if env_example.exists() else '[red](없음)[/red]'}")

    if env_file.exists():
        console.print("\n[bold]현재 설정[/bold]")
        content = env_file.read_text()
        for line in content.splitlines():
            if line.strip() and not line.startswith("#"):
                key = line.split("=")[0]
                if "TOKEN" in key or "SECRET" in key or "PASSWORD" in key:
                    console.print(f"  {key}=****")
                else:
                    console.print(f"  {line}")
    else:
        console.print("\n[yellow]![/yellow] 환경 파일이 없습니다.")
        console.print("  생성: sudo vaultctl setup env --edit")


@app.command("test")
def test_connection():
    """Vault 연결 및 인증 테스트."""
    from vaultctl.commands.auth import ensure_authenticated

    console.print("[bold]연결 테스트[/bold]\n")

    # 1. 서버 연결
    console.print(f"1. Vault 서버: {settings.vault_addr}")
    client = VaultClient()
    health = client.health()

    if health.get("initialized") and not health.get("sealed"):
        console.print("   [green]✓[/green] 연결 성공")
    else:
        console.print("   [red]✗[/red] 연결 실패")
        raise typer.Exit(1)

    # 2. 인증
    console.print("\n2. 인증 확인")
    try:
        client = ensure_authenticated()
        console.print("   [green]✓[/green] 인증 성공")
    except typer.Exit:
        console.print("   [red]✗[/red] 인증 실패")
        raise

    # 3. KV 엔진 확인
    console.print(f"\n3. KV 엔진: {settings.kv_mount}/")
    try:
        client.kv_list(settings.kv_mount, "")
        console.print("   [green]✓[/green] 접근 가능")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")

    console.print("\n[green]✓[/green] 테스트 완료")


def _write_env_file(path: Path, vault_addr: str, vault_token: str) -> None:
    """환경 파일 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""# vaultctl 환경 설정
# Generated by vaultctl setup init

VAULT_ADDR={vault_addr}
VAULT_TOKEN={vault_token}

VAULTCTL_VAULT_ADDR={vault_addr}
VAULTCTL_TOKEN_RENEW_THRESHOLD=3600
VAULTCTL_TOKEN_RENEW_INCREMENT=86400
"""
    path.write_text(content)
    path.chmod(0o600)
    console.print(f"[green]✓[/green] 환경 파일 생성: {path}")


def _setup_systemd_timer() -> None:
    """systemd 타이머 활성화."""
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "vaultctl-renew.timer"], check=True)
        subprocess.run(["systemctl", "start", "vaultctl-renew.timer"], check=True)
        console.print("[green]✓[/green] systemd 타이머 활성화 완료")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] systemd 설정 실패: {e}")
        raise typer.Exit(1)


def _disable_systemd_timer() -> None:
    """systemd 타이머 비활성화."""
    try:
        subprocess.run(["systemctl", "stop", "vaultctl-renew.timer"], check=False)
        subprocess.run(["systemctl", "disable", "vaultctl-renew.timer"], check=False)
        console.print("[green]✓[/green] systemd 타이머 비활성화 완료")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] systemd 설정 실패: {e}")


def _show_systemd_status() -> None:
    """systemd 상태 출력."""
    console.print("[bold]systemd 타이머 상태[/bold]\n")

    # 타이머 상태
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
    console.print(f"  타이머: {status_icon} {'활성' if is_active else '비활성'}")
    console.print(f"  자동시작: {'예' if is_enabled else '아니오'}")

    # 다음 실행 시간
    if is_active:
        result = subprocess.run(
            ["systemctl", "show", "vaultctl-renew.timer", "--property=NextElapseUSecRealtime"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            next_run = result.stdout.strip().split("=")[1] if "=" in result.stdout else "알 수 없음"
            console.print(f"  다음 실행: {next_run}")

    # 마지막 실행 결과
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
                console.print(f"  마지막 결과: {result_icon} {last_result}")
