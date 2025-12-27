"""Docker 환경변수 관리 명령어."""

import json
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from vaultctl.config import settings
from vaultctl.vault_client import VaultError
from vaultctl.utils import create_kv_table, load_env_file, parse_key_value_args, write_env_file
from vaultctl.commands.auth import ensure_authenticated

app = typer.Typer(help="Docker 환경변수 관리")
console = Console()


def _get_docker_path(name: str) -> str:
    """Docker 경로 생성."""
    return f"{settings.kv_docker_path}/{name}"


@app.command("list")
def list_docker():
    """등록된 Docker 서비스 목록 조회."""
    client = ensure_authenticated()

    try:
        items = client.kv_list(settings.kv_mount, settings.kv_docker_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] 등록된 Docker 서비스가 없습니다.")
        return

    table = Table(title="등록된 Docker 서비스", show_header=True, header_style="bold cyan")
    table.add_column("서비스명", style="green")
    table.add_column("변수 수", style="white")

    for item in sorted(items):
        name = item.rstrip("/")
        try:
            data = client.kv_get(settings.kv_mount, _get_docker_path(name))
            table.add_row(name, str(len(data)))
        except VaultError:
            table.add_row(name, "[red]-[/red]")

    console.print(table)


@app.command("get")
def get_docker(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    raw: bool = typer.Option(False, "--raw", help="값만 출력 (스크립트용)"),
):
    """Docker 서비스 환경변수 조회."""
    client = ensure_authenticated()

    try:
        data = client.kv_get(settings.kv_mount, _get_docker_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] 서비스를 찾을 수 없습니다: {name}")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        table = create_kv_table(data, title=f"Docker: {name}")
        console.print(table)


@app.command("put")
def put_docker(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    data: list[str] = typer.Argument(..., help="KEY=value 쌍들"),
    merge: bool = typer.Option(True, "--merge/--replace", help="기존 값과 병합 (기본) / 교체"),
):
    """Docker 서비스 환경변수 저장."""
    client = ensure_authenticated()

    new_data = parse_key_value_args(data)
    if not new_data:
        console.print("[red]✗[/red] KEY=value 형식으로 데이터를 입력하세요.")
        raise typer.Exit(1)

    if merge:
        try:
            existing = client.kv_get(settings.kv_mount, _get_docker_path(name))
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass

    try:
        client.kv_put(settings.kv_mount, _get_docker_path(name), new_data)
        console.print(f"[green]✓[/green] 저장 완료: {name}")
        table = create_kv_table(new_data, title=f"Docker: {name}")
        console.print(table)
    except VaultError as e:
        console.print(f"[red]✗[/red] 저장 실패: {e.message}")
        raise typer.Exit(1)


@app.command("delete")
def delete_docker(
    name: str = typer.Argument(..., help="서비스명"),
    force: bool = typer.Option(False, "--force", "-f", help="확인 없이 삭제"),
):
    """Docker 서비스 환경변수 삭제."""
    client = ensure_authenticated()

    if not force:
        confirm = typer.confirm(f"정말 '{name}'을(를) 삭제하시겠습니까?")
        if not confirm:
            console.print("취소됨")
            raise typer.Exit(0)

    try:
        client.kv_delete(settings.kv_mount, _get_docker_path(name))
        console.print(f"[green]✓[/green] 삭제 완료: {name}")
    except VaultError as e:
        console.print(f"[red]✗[/red] 삭제 실패: {e.message}")
        raise typer.Exit(1)


@app.command("env")
def generate_env(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    output: Path = typer.Option(Path(".env"), "--output", "-o", help="출력 파일"),
    stdout: bool = typer.Option(False, "--stdout", help="stdout으로 출력"),
):
    """Vault에서 .env 파일 생성."""
    client = ensure_authenticated()

    try:
        data = client.kv_get(settings.kv_mount, _get_docker_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] 서비스를 찾을 수 없습니다: {name}")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] 데이터 없음: {name}")
        raise typer.Exit(1)

    if stdout:
        for key, value in sorted(data.items()):
            console.print(f"{key}={value}")
    else:
        write_env_file(str(output), data, header=f"Generated from Vault: {name}")
        console.print(f"[green]✓[/green] 환경변수 파일 생성: {output}")


@app.command("import-env")
def import_env(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    file: Path = typer.Option(Path(".env"), "--file", "-f", help="입력 .env 파일"),
    merge: bool = typer.Option(True, "--merge/--replace", help="기존 값과 병합 (기본) / 교체"),
):
    """기존 .env 파일을 Vault에 저장."""
    client = ensure_authenticated()

    if not file.exists():
        console.print(f"[red]✗[/red] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(1)

    new_data = load_env_file(str(file))
    if not new_data:
        console.print(f"[yellow]![/yellow] 유효한 환경변수가 없습니다: {file}")
        raise typer.Exit(1)

    if merge:
        try:
            existing = client.kv_get(settings.kv_mount, _get_docker_path(name))
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass

    try:
        client.kv_put(settings.kv_mount, _get_docker_path(name), new_data)
        console.print(f"[green]✓[/green] 저장 완료: {name} ({len(new_data)}개 변수)")
    except VaultError as e:
        console.print(f"[red]✗[/red] 저장 실패: {e.message}")
        raise typer.Exit(1)


@app.command("compose")
def docker_compose(
    name: str = typer.Argument(..., help="서비스명 (예: n8n)"),
    args: list[str] = typer.Argument(None, help="docker-compose 명령어 인자"),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e", help=".env 파일 경로"),
):
    """Vault에서 환경변수를 로드하고 docker-compose 실행."""
    client = ensure_authenticated()

    try:
        data = client.kv_get(settings.kv_mount, _get_docker_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] 서비스를 찾을 수 없습니다: {name}")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    # .env 파일 생성
    write_env_file(str(env_file), data, header=f"Generated from Vault: {name}")
    console.print(f"[dim]환경변수 로드: {len(data)}개[/dim]")

    # docker-compose 실행
    if args:
        cmd = ["docker-compose", *args]
        console.print(f"[dim]실행: {' '.join(cmd)}[/dim]")
        result = subprocess.run(cmd)
        raise typer.Exit(result.returncode)
    else:
        console.print(f"[green]✓[/green] .env 파일 생성 완료: {env_file}")
        console.print("  실행: docker-compose up -d")
