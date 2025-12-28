"""LXC 관리 명령어."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from vaultctl.commands.auth import ensure_authenticated
from vaultctl.config import settings
from vaultctl.utils import copy_to_clipboard, create_kv_table, parse_key_value_args
from vaultctl.vault_client import VaultError

app = typer.Typer(help="LXC 컨테이너 정보 관리")
console = Console()


def _get_lxc_path(name: str) -> str:
    """LXC 경로 생성."""
    return f"{settings.kv_lxc_path}/{name}"

def _get_auth_client():
    """인증된 VaultClient 반환."""
    client = ensure_authenticated()
    return client


@app.command("list")
def list_lxc(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="상세 정보 출력"),
):
    """등록된 LXC 목록 조회."""
    try:
        items = _get_auth_client().kv_list(settings.kv_mount, settings.kv_lxc_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] 등록된 LXC가 없습니다.")
        return

    table = Table(title="등록된 LXC 목록", show_header=True, header_style="bold cyan")
    table.add_column("이름", style="green")

    if verbose:
        table.add_column("IP", style="white")
        table.add_column("비고", style="dim")

        for item in sorted(items):
            name = item.rstrip("/")
            try:
                data = _get_auth_client().kv_get(settings.kv_mount, _get_lxc_path(name))
                ip = data.get("ip", "-")
                notes = data.get("notes", "-")
                table.add_row(name, ip, notes[:40] + "..." if len(notes) > 40 else notes)
            except VaultError:
                table.add_row(name, "-", "[red]조회 실패[/red]")
    else:
        for item in sorted(items):
            table.add_row(item.rstrip("/"))

    console.print(table)
    console.print(f"\n총 {len(items)}개")


@app.command("get")
def get_lxc(
    name: str = typer.Argument(..., help="LXC 이름 (예: 130-n8n)"),
    field: Optional[str] = typer.Option(None, "--field", "-f", help="특정 필드만 조회"),
    copy: bool = typer.Option(False, "--copy", "-c", help="값을 클립보드에 복사"),
    raw: bool = typer.Option(False, "--raw", help="값만 출력 (스크립트용)"),
):
    """LXC 정보 조회."""
    try:
        data = _get_auth_client().kv_get(settings.kv_mount, _get_lxc_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] LXC를 찾을 수 없습니다: {name}")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] 데이터 없음: {name}")
        raise typer.Exit(1)

    # 특정 필드만 조회
    if field:
        if field not in data:
            console.print(f"[red]✗[/red] 필드를 찾을 수 없습니다: {field}")
            console.print(f"  사용 가능: {', '.join(data.keys())}")
            raise typer.Exit(1)

        value = str(data[field])

        if copy:
            if copy_to_clipboard(value):
                if not raw:
                    console.print(f"[green]✓[/green] 클립보드에 복사됨: {name}/{field}")
            else:
                if not raw:
                    console.print(f"[yellow]![/yellow] 클립보드 복사 실패")
                console.print(value)
        elif raw:
            console.print(value)
        else:
            console.print(f"[bold]{field}[/bold]: {value}")
        return

    # 전체 조회
    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        table = create_kv_table(data, title=f"LXC: {name}")
        console.print(table)


@app.command("put")
def put_lxc(
    name: str = typer.Argument(..., help="LXC 이름 (예: 130-n8n)"),
    data: list[str] = typer.Argument(..., help="key=value 쌍들"),
    merge: bool = typer.Option(True, "--merge/--replace", help="기존 값과 병합 (기본) / 교체"),
):
    """LXC 정보 저장."""
    new_data = parse_key_value_args(data)
    if not new_data:
        console.print("[red]✗[/red] key=value 형식으로 데이터를 입력하세요.")
        console.print("  예: vaultctl lxc put 130-n8n root_password=xxx ip=10.10.10.130")
        raise typer.Exit(1)

    # 기존 값과 병합
    if merge:
        try:
            existing = _get_auth_client().kv_get(settings.kv_mount, _get_lxc_path(name))
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass  # 새로 생성

    try:
        _get_auth_client().kv_put(settings.kv_mount, _get_lxc_path(name), new_data)
        console.print(f"[green]✓[/green] 저장 완료: {name}")

        # 저장된 내용 표시
        table = create_kv_table(new_data, title=f"LXC: {name}")
        console.print(table)

    except VaultError as e:
        console.print(f"[red]✗[/red] 저장 실패: {e.message}")
        raise typer.Exit(1)


@app.command("delete")
def delete_lxc(
    name: str = typer.Argument(..., help="LXC 이름"),
    force: bool = typer.Option(False, "--force", "-f", help="확인 없이 삭제"),
):
    """LXC 정보 삭제."""
    if not force:
        confirm = typer.confirm(f"정말 '{name}'을(를) 삭제하시겠습니까?")
        if not confirm:
            console.print("취소됨")
            raise typer.Exit(0)

    try:
        _get_auth_client().kv_delete(settings.kv_mount, _get_lxc_path(name))
        console.print(f"[green]✓[/green] 삭제 완료: {name}")
    except VaultError as e:
        console.print(f"[red]✗[/red] 삭제 실패: {e.message}")
        raise typer.Exit(1)


@app.command("pass")
def get_password(
    name: str = typer.Argument(..., help="LXC 이름"),
    field: str = typer.Option("root_password", "--field", "-f", help="비밀번호 필드명"),
):
    """LXC 비밀번호를 클립보드에 복사."""
    try:
        data = _get_auth_client().kv_get(settings.kv_mount, _get_lxc_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] LXC를 찾을 수 없습니다: {name}")
        else:
            console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if field not in data:
        console.print(f"[red]✗[/red] 필드를 찾을 수 없습니다: {field}")
        console.print(f"  사용 가능: {', '.join(data.keys())}")
        raise typer.Exit(1)

    value = str(data[field])

    if copy_to_clipboard(value):
        console.print(f"[green]✓[/green] 클립보드에 복사됨: {name}/{field}")
    else:
        console.print(f"[yellow]![/yellow] 클립보드 복사 실패. 값:")
        console.print(value)


@app.command("import")
def import_lxc(
    file: Path = typer.Argument(..., help="JSON 파일 경로"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="실제 저장 없이 검증만"),
):
    """JSON 파일에서 LXC 정보 일괄 등록."""
    if not file.exists():
        console.print(f"[red]✗[/red] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(1)

    try:
        with open(file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] JSON 파싱 오류: {e}")
        raise typer.Exit(1)

    # _설명 등 메타 필드 제거
    data = {k: v for k, v in data.items() if not k.startswith("_")}

    if not data:
        console.print("[yellow]![/yellow] 등록할 LXC가 없습니다.")
        return

    console.print(f"[dim]총 {len(data)}개 LXC 등록 {'(dry-run)' if dry_run else ''}...[/dim]")

    success = 0
    failed = 0

    for name, lxc_data in data.items():
        if not isinstance(lxc_data, dict):
            console.print(f"  [red]✗[/red] {name}: 잘못된 형식")
            failed += 1
            continue

        # 빈 값 제거
        lxc_data = {k: v for k, v in lxc_data.items() if v}

        if dry_run:
            console.print(f"  [dim]○[/dim] {name}: {len(lxc_data)}개 필드")
            success += 1
        else:
            try:
                _get_auth_client().kv_put(settings.kv_mount, _get_lxc_path(name), lxc_data)
                console.print(f"  [green]✓[/green] {name}")
                success += 1
            except VaultError as e:
                console.print(f"  [red]✗[/red] {name}: {e.message}")
                failed += 1

    console.print(f"\n완료: {success}개 성공, {failed}개 실패")


@app.command("export")
def export_lxc(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="출력 파일 (생략 시 stdout)"),
):
    """등록된 모든 LXC를 JSON으로 내보내기."""
    try:
        items = _get_auth_client().kv_list(settings.kv_mount, settings.kv_lxc_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] 등록된 LXC가 없습니다.")
        return

    result = {}
    for item in items:
        name = item.rstrip("/")
        try:
            data = _get_auth_client().kv_get(settings.kv_mount, _get_lxc_path(name))
            result[name] = data
        except VaultError:
            result[name] = {}

    json_output = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        output.write_text(json_output)
        console.print(f"[green]✓[/green] 내보내기 완료: {output}")
    else:
        console.print(json_output)
