"""Vault CLI 메인 애플리케이션."""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__
from .commands import auth, docker, lxc, setup, token, extended
from .config import settings

app = typer.Typer(
    name="vaultctl",
    help="HashiCorp Vault CLI with 1Password integration",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# 서브 명령어 등록
app.add_typer(auth.app, name="auth", help="인증 관리")
app.add_typer(lxc.app, name="lxc", help="LXC 컨테이너 정보 관리")
app.add_typer(docker.app, name="docker", help="Docker 환경변수 관리")
app.add_typer(token.app, name="token", help="토큰 관리")
app.add_typer(setup.app, name="setup", help="초기 설정 및 systemd 관리")

# 확장 명령어 직접 등록 (teller 스타일)
app.command("run")(extended.run_command)
app.command("sh")(extended.shell_export)
app.command("scan")(extended.scan_secrets)
app.command("redact")(extended.redact_secrets)
app.command("watch")(extended.watch_and_restart)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="버전 출력"),
    vault_addr: Optional[str] = typer.Option(
        None,
        "--vault-addr",
        "-a",
        envvar="VAULT_ADDR",
        help="Vault 서버 주소",
    ),
    vault_token: Optional[str] = typer.Option(
        None,
        "--vault-token",
        "-t",
        envvar="VAULT_TOKEN",
        help="Vault 토큰",
    ),
):
    """HashiCorp Vault CLI with 1Password integration.

    Proxmox LXC 비밀번호/URL, Docker 환경변수를 Vault로 중앙 관리합니다.

    \b
    시작하기:
        vaultctl auth login       # 1Password에서 토큰 로드
        vaultctl lxc list         # LXC 목록 조회
        vaultctl lxc get 130-n8n  # LXC 정보 조회
        vaultctl lxc pass 130-n8n # 비밀번호 클립보드 복사

    \b
    Docker 환경변수:
        vaultctl docker env n8n           # .env 파일 생성
        vaultctl docker import-env n8n    # 기존 .env를 Vault에 저장
        vaultctl docker compose n8n up -d # 환경변수 로드 후 docker-compose 실행

    \b
    설정:
        환경변수 또는 ~/.config/vaultctl/config.env 사용
        VAULTCTL_VAULT_ADDR=https://vault.example.com
        VAULTCTL_OP_VAULT=Infrastructure
        VAULTCTL_OP_ITEM=vault-token
    """
    if version:
        console.print(f"vaultctl {__version__}")
        raise typer.Exit(0)

    # 전역 옵션 적용
    if vault_addr:
        settings.vault_addr = vault_addr
    if vault_token:
        settings.vault_token = vault_token


# ─────────────────────────────────────────────────────────────────────────────
# 단축 명령어 (자주 쓰는 것들)
# ─────────────────────────────────────────────────────────────────────────────


@app.command("login", hidden=True)
def quick_login():
    """[단축] auth login."""
    auth.login(token=None, force=False)


@app.command("status", hidden=True)
def quick_status():
    """[단축] auth status."""
    auth.status()


@app.command("ls")
def quick_list(
    type: str = typer.Argument("lxc", help="타입: lxc, docker"),
):
    """[단축] 목록 조회."""
    if type == "lxc":
        lxc.list_lxc(verbose=False)
    elif type == "docker":
        docker.list_docker()
    else:
        console.print(f"[red]✗[/red] 알 수 없는 타입: {type}")
        console.print("  사용 가능: lxc, docker")
        raise typer.Exit(1)


@app.command("get")
def quick_get(
    name: str = typer.Argument(..., help="이름 (예: 130-n8n)"),
    type: str = typer.Option("lxc", "--type", "-t", help="타입: lxc, docker"),
):
    """[단축] 정보 조회."""
    if type == "lxc":
        lxc.get_lxc(name=name, field=None, copy=False, raw=False)
    elif type == "docker":
        docker.get_docker(name=name, raw=False)
    else:
        console.print(f"[red]✗[/red] 알 수 없는 타입: {type}")
        raise typer.Exit(1)


@app.command("pass")
def quick_pass(
    name: str = typer.Argument(..., help="LXC 이름 (예: 130-n8n)"),
    field: str = typer.Option("root_password", "--field", "-f", help="필드명"),
):
    """[단축] 비밀번호 클립보드 복사."""
    lxc.get_password(name=name, field=field)


@app.command("env")
def quick_env(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    output: str = typer.Option(".env", "--output", "-o", help="출력 파일"),
):
    """[단축] docker env - .env 파일 생성."""
    from pathlib import Path

    docker.generate_env(name=name, output=Path(output), stdout=False)


# ─────────────────────────────────────────────────────────────────────────────
# 설정 명령어
# ─────────────────────────────────────────────────────────────────────────────


@app.command("config")
def show_config():
    """현재 설정 출력."""
    from rich.table import Table

    table = Table(title="현재 설정", show_header=True, header_style="bold cyan")
    table.add_column("설정", style="green")
    table.add_column("값", style="white")
    table.add_column("환경변수", style="dim")

    configs = [
        ("Vault 주소", settings.vault_addr, "VAULTCTL_VAULT_ADDR"),
        ("KV 마운트", settings.kv_mount, "VAULTCTL_KV_MOUNT"),
        ("LXC 경로", settings.kv_lxc_path, "VAULTCTL_KV_LXC_PATH"),
        ("Docker 경로", settings.kv_docker_path, "VAULTCTL_KV_DOCKER_PATH"),
        ("1Password Vault", settings.op_vault, "VAULTCTL_OP_VAULT"),
        ("1Password Item", settings.op_item, "VAULTCTL_OP_ITEM"),
        ("토큰 갱신 임계값", f"{settings.token_renew_threshold}초", "VAULTCTL_TOKEN_RENEW_THRESHOLD"),
    ]

    for name, value, env in configs:
        table.add_row(name, str(value), env)

    console.print(table)
    console.print(f"\n설정 디렉토리: {settings.config_dir}")
    console.print(f"캐시 디렉토리: {settings.cache_dir}")


if __name__ == "__main__":
    app()
