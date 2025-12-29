"""Admin commands for vaultctl.
관리자 전용 명령어.

Usage:
    vaultctl admin setup vault        # Vault policy, AppRole 생성
    vaultctl admin setup apt-server   # APT 저장소 서버 구축
    vaultctl admin setup apt-client   # APT 클라이언트 설정
    
    vaultctl admin list               # 시크릿 목록
    vaultctl admin get <name>         # 시크릿 조회
    vaultctl admin put <name> K=V     # 시크릿 저장
    vaultctl admin delete <name>      # 시크릿 삭제
    
    vaultctl admin token status       # 토큰 상태
    vaultctl admin token renew        # 토큰 갱신
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from vaultctl.config import settings
from vaultctl.utils import copy_to_clipboard, create_kv_table, format_duration, parse_key_value_args
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(
    name="admin",
    help="Administrator commands / 관리자 명령어",
    no_args_is_help=True,
)
console = Console()

# Sub-apps
setup_app = typer.Typer(help="Setup commands / 설정 명령어")
token_app = typer.Typer(help="Token management / 토큰 관리")

app.add_typer(setup_app, name="setup")
app.add_typer(token_app, name="token")

# Import repo commands
from vaultctl.commands import repo
app.add_typer(repo.app, name="repo", help="APT package management / APT 패키지 관리")


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client / 인증된 클라이언트 반환."""
    client = VaultClient()
    
    # Try cached token
    if settings.token_cache_file.exists():
        try:
            token = settings.token_cache_file.read_text().strip()
            if token:
                client = VaultClient(token=token)
                if client.is_authenticated():
                    return client
        except PermissionError:
            pass
    
    # Try config token
    if settings.vault_token:
        client = VaultClient(token=settings.vault_token)
        if client.is_authenticated():
            return client
    
    # Try AppRole
    if settings.has_approle_credentials():
        try:
            result = client.approle_login(
                settings.approle_role_id,
                settings.approle_secret_id,
                settings.approle_mount,
            )
            token = result.get("auth", {}).get("client_token")
            if token:
                client = VaultClient(token=token)
                return client
        except VaultError:
            pass
    
    console.print("[red]✗[/red] 인증이 필요합니다.")
    console.print("  실행: vaultctl init")
    raise typer.Exit(1)


def _get_secret_path(name: str) -> str:
    """Get KV secret path / 시크릿 경로 생성."""
    return f"{settings.kv_lxc_path}/{name}"


# ═══════════════════════════════════════════════════════════════════════════════
# Secret Management Commands (list, get, put, delete)
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("list")
def list_secrets(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="상세 정보 출력"),
):
    """List all secrets / 시크릿 목록 조회.
    
    Examples:
        vaultctl admin list
        vaultctl admin list -v
    """
    client = _get_authenticated_client()
    
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_lxc_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] 등록된 시크릿이 없습니다.")
        console.print(f"  경로: {settings.kv_mount}/{settings.kv_lxc_path}/")
        return

    table = Table(title="시크릿 목록", show_header=True, header_style="bold cyan")
    table.add_column("이름", style="green")

    if verbose:
        table.add_column("키 개수", style="white")
        table.add_column("키 목록", style="dim")

        for item in sorted(items):
            name = item.rstrip("/")
            try:
                data = client.kv_get(settings.kv_mount, _get_secret_path(name))
                keys = ", ".join(sorted(data.keys()))
                if len(keys) > 50:
                    keys = keys[:50] + "..."
                table.add_row(name, str(len(data)), keys)
            except VaultError:
                table.add_row(name, "-", "[red]조회 실패[/red]")
    else:
        for item in sorted(items):
            table.add_row(item.rstrip("/"))

    console.print(table)
    console.print(f"\n총 {len(items)}개")


@app.command("get")
def get_secret(
    name: str = typer.Argument(..., help="시크릿 이름 (예: lxc-161)"),
    field: Optional[str] = typer.Option(None, "--field", "-f", help="특정 필드만 조회"),
    copy: bool = typer.Option(False, "--copy", "-c", help="값을 클립보드에 복사"),
    raw: bool = typer.Option(False, "--raw", help="JSON으로 출력"),
):
    """Get secret / 시크릿 조회.
    
    Examples:
        vaultctl admin get lxc-161
        vaultctl admin get lxc-161 -f DB_PASSWORD
        vaultctl admin get lxc-161 -f DB_PASSWORD --copy
        vaultctl admin get lxc-161 --raw
    """
    client = _get_authenticated_client()
    
    try:
        data = client.kv_get(settings.kv_mount, _get_secret_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] 시크릿을 찾을 수 없습니다: {name}")
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
                console.print(f"[green]✓[/green] 클립보드에 복사됨: {name}/{field}")
            else:
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
        table = create_kv_table(data, title=f"Secret: {name}")
        console.print(table)


@app.command("put")
def put_secret(
    name: str = typer.Argument(..., help="시크릿 이름 (예: lxc-161)"),
    data: list[str] = typer.Argument(..., help="KEY=value 쌍들"),
    merge: bool = typer.Option(True, "--merge/--replace", help="기존 값과 병합 (기본) / 교체"),
):
    """Put secret / 시크릿 저장.
    
    Examples:
        vaultctl admin put lxc-161 DB_HOST=postgres.local DB_PASSWORD=secret
        vaultctl admin put lxc-161 NEW_KEY=value --merge
        vaultctl admin put lxc-161 ONLY_THIS=value --replace
    """
    client = _get_authenticated_client()
    
    new_data = parse_key_value_args(data)
    if not new_data:
        console.print("[red]✗[/red] KEY=value 형식으로 데이터를 입력하세요.")
        console.print("  예: vaultctl admin put lxc-161 DB_HOST=localhost DB_PASSWORD=secret")
        raise typer.Exit(1)

    # 기존 값과 병합
    if merge:
        try:
            existing = client.kv_get(settings.kv_mount, _get_secret_path(name))
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass  # 새로 생성

    try:
        client.kv_put(settings.kv_mount, _get_secret_path(name), new_data)
        console.print(f"[green]✓[/green] 저장 완료: {name}")

        # 저장된 내용 표시
        table = create_kv_table(new_data, title=f"Secret: {name}")
        console.print(table)

    except VaultError as e:
        console.print(f"[red]✗[/red] 저장 실패: {e.message}")
        raise typer.Exit(1)


@app.command("delete")
def delete_secret(
    name: str = typer.Argument(..., help="시크릿 이름"),
    force: bool = typer.Option(False, "--force", "-f", help="확인 없이 삭제"),
):
    """Delete secret / 시크릿 삭제.
    
    Examples:
        vaultctl admin delete lxc-161
        vaultctl admin delete lxc-161 --force
    """
    client = _get_authenticated_client()
    
    if not force:
        confirm = typer.confirm(f"정말 '{name}'을(를) 삭제하시겠습니까?")
        if not confirm:
            console.print("취소됨")
            raise typer.Exit(0)

    try:
        client.kv_delete(settings.kv_mount, _get_secret_path(name))
        console.print(f"[green]✓[/green] 삭제 완료: {name}")
    except VaultError as e:
        console.print(f"[red]✗[/red] 삭제 실패: {e.message}")
        raise typer.Exit(1)


@app.command("import")
def import_secrets(
    file: Path = typer.Argument(..., help="JSON 파일 경로"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="실제 저장 없이 검증만"),
):
    """Import secrets from JSON file / JSON 파일에서 시크릿 일괄 등록.
    
    JSON format:
        {
            "lxc-161": {"DB_HOST": "localhost", "DB_PASSWORD": "secret"},
            "lxc-162": {"REDIS_URL": "redis://localhost:6379"}
        }
    
    Examples:
        vaultctl admin import secrets.json
        vaultctl admin import secrets.json --dry-run
    """
    client = _get_authenticated_client()
    
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
        console.print("[yellow]![/yellow] 등록할 시크릿이 없습니다.")
        return

    console.print(f"[dim]총 {len(data)}개 시크릿 등록 {'(dry-run)' if dry_run else ''}...[/dim]")

    success = 0
    failed = 0

    for name, secret_data in data.items():
        if not isinstance(secret_data, dict):
            console.print(f"  [red]✗[/red] {name}: 잘못된 형식")
            failed += 1
            continue

        # 빈 값 제거
        secret_data = {k: v for k, v in secret_data.items() if v}

        if dry_run:
            console.print(f"  [dim]○[/dim] {name}: {len(secret_data)}개 필드")
            success += 1
        else:
            try:
                client.kv_put(settings.kv_mount, _get_secret_path(name), secret_data)
                console.print(f"  [green]✓[/green] {name}")
                success += 1
            except VaultError as e:
                console.print(f"  [red]✗[/red] {name}: {e.message}")
                failed += 1

    console.print(f"\n완료: {success}개 성공, {failed}개 실패")


@app.command("export")
def export_secrets(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="출력 파일 (생략 시 stdout)"),
):
    """Export all secrets to JSON / 모든 시크릿을 JSON으로 내보내기.
    
    Examples:
        vaultctl admin export
        vaultctl admin export -o secrets.json
    """
    client = _get_authenticated_client()
    
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_lxc_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] 조회 실패: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] 등록된 시크릿이 없습니다.")
        return

    result = {}
    for item in items:
        name = item.rstrip("/")
        try:
            data = client.kv_get(settings.kv_mount, _get_secret_path(name))
            result[name] = data
        except VaultError:
            result[name] = {}

    json_output = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        output.write_text(json_output)
        console.print(f"[green]✓[/green] 내보내기 완료: {output}")
    else:
        console.print(json_output)


# ═══════════════════════════════════════════════════════════════════════════════
# Setup Commands
# ═══════════════════════════════════════════════════════════════════════════════


@setup_app.command("vault")
def setup_vault():
    """Setup Vault policy and AppRole / Vault 정책 및 AppRole 생성.
    
    Creates:
        - Policy: vaultctl (read/write to proxmox/*)
        - AppRole: vaultctl-role
    
    Requires root token or admin privileges.
    
    Examples:
        vaultctl admin setup vault
    """
    console.print(Panel.fit(
        "[bold blue]Vault Setup[/bold blue]\n\n"
        "This will create:\n"
        "• Policy: vaultctl\n"
        "• AppRole: vaultctl-role\n"
        "• KV secrets engine: proxmox/",
        title="🔐 Vault Setup",
    ))
    console.print()
    
    # Get admin token
    vault_addr = Prompt.ask(
        "Vault server address",
        default=settings.vault_addr,
    )
    admin_token = Prompt.ask("Root/Admin token", password=True)
    
    client = VaultClient(addr=vault_addr, token=admin_token)
    
    # Test connection
    console.print("\n[dim]Testing connection...[/dim]")
    try:
        client.token_lookup()
        console.print("[green]✓[/green] Connected")
    except VaultError as e:
        console.print(f"[red]✗[/red] Connection failed: {e.message}")
        raise typer.Exit(1)
    
    # 1. Enable KV secrets engine
    console.print("\n[bold]1. KV Secrets Engine[/bold]")
    kv_mount = Prompt.ask("KV mount path", default=settings.kv_mount)
    
    try:
        # Check if already enabled
        mounts = client._request("GET", "/sys/mounts")
        if f"{kv_mount}/" in mounts.get("data", {}):
            console.print(f"   [green]✓[/green] Already enabled: {kv_mount}/")
        else:
            client._request("POST", f"/sys/mounts/{kv_mount}", json={
                "type": "kv",
                "options": {"version": "2"},
            })
            console.print(f"   [green]✓[/green] Enabled: {kv_mount}/")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    # 2. Create policy
    console.print("\n[bold]2. Policy[/bold]")
    policy_name = "vaultctl"
    policy_hcl = f'''
# vaultctl policy
# Read/write access to {kv_mount}/*

path "{kv_mount}/data/*" {{
  capabilities = ["create", "read", "update", "delete", "list"]
}}

path "{kv_mount}/metadata/*" {{
  capabilities = ["list", "read", "delete"]
}}

path "auth/token/renew-self" {{
  capabilities = ["update"]
}}

path "auth/token/lookup-self" {{
  capabilities = ["read"]
}}
'''
    
    try:
        client._request("PUT", f"/sys/policies/acl/{policy_name}", json={
            "policy": policy_hcl,
        })
        console.print(f"   [green]✓[/green] Created: {policy_name}")
    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)
    
    # 3. Enable AppRole auth
    console.print("\n[bold]3. AppRole Auth[/bold]")
    try:
        auth_methods = client._request("GET", "/sys/auth")
        if "approle/" in auth_methods.get("data", {}):
            console.print("   [green]✓[/green] Already enabled: approle/")
        else:
            client._request("POST", "/sys/auth/approle", json={
                "type": "approle",
            })
            console.print("   [green]✓[/green] Enabled: approle/")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    # 4. Create AppRole
    console.print("\n[bold]4. AppRole[/bold]")
    role_name = "vaultctl"
    
    try:
        client._request("POST", f"/auth/approle/role/{role_name}", json={
            "token_policies": [policy_name],
            "token_ttl": "1h",
            "token_max_ttl": "24h",
            "secret_id_ttl": "0",  # Never expires
            "secret_id_num_uses": 0,  # Unlimited
        })
        console.print(f"   [green]✓[/green] Created: {role_name}")
    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)
    
    # 5. Get Role ID and Secret ID
    console.print("\n[bold]5. Credentials[/bold]")
    try:
        role_id_resp = client._request("GET", f"/auth/approle/role/{role_name}/role-id")
        role_id = role_id_resp.get("data", {}).get("role_id")
        
        secret_id_resp = client._request("POST", f"/auth/approle/role/{role_name}/secret-id")
        secret_id = secret_id_resp.get("data", {}).get("secret_id")
        
        console.print(f"\n[yellow]{'─' * 60}[/yellow]")
        console.print("[yellow]Save these credentials securely![/yellow]")
        console.print(f"[yellow]{'─' * 60}[/yellow]")
        console.print(f"\n  Role ID:    {role_id}")
        console.print(f"  Secret ID:  {secret_id}")
        console.print(f"\n[yellow]{'─' * 60}[/yellow]")
        
    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)
    
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Use these credentials with:\n"
        f"  vaultctl init\n\n"
        "Or set environment variables:\n"
        f"  export VAULT_ADDR={vault_addr}\n"
        f"  export VAULT_ROLE_ID={role_id}\n"
        f"  export VAULT_SECRET_ID={secret_id}",
        title="✓ Complete",
    ))


@setup_app.command("apt-server")
def setup_apt_server():
    """Setup APT repository server / APT 저장소 서버 구축.
    
    See: vaultctl admin setup apt-server --help
    """
    # Import from setup.py
    from vaultctl.commands.setup import apt_server_setup
    apt_server_setup(reconfigure=False)


@setup_app.command("apt-client")
def setup_apt_client(
    url: str = typer.Argument(..., help="APT repository URL"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Auth username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Auth password"),
):
    """Setup APT client / APT 클라이언트 설정.
    
    Examples:
        vaultctl admin setup apt-client https://apt.example.com
        vaultctl admin setup apt-client https://apt.example.com -u apt -p secret
    """
    from vaultctl.commands.setup import apt_client_setup
    apt_client_setup(url=url, user=user, password=password, codename="stable", remove=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Token Commands
# ═══════════════════════════════════════════════════════════════════════════════


@token_app.command("status")
def token_status():
    """Show token status / 토큰 상태 확인.
    
    Examples:
        vaultctl admin token status
    """
    client = _get_authenticated_client()
    
    try:
        token_info = client.token_lookup()
    except VaultError as e:
        console.print(f"[red]✗[/red] {e.message}")
        raise typer.Exit(1)

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
    
    creation_time = data.get("creation_time", "-")
    if isinstance(creation_time, (int, float)):
        from datetime import datetime
        creation_time = datetime.fromtimestamp(creation_time).strftime("%Y-%m-%d %H:%M:%S")
    table.add_row("Creation Time", str(creation_time))

    console.print(Panel(table, title="토큰 정보", border_style="blue"))


@token_app.command("renew")
def token_renew():
    """Renew token / 토큰 갱신.
    
    Examples:
        vaultctl admin token renew
    """
    client = _get_authenticated_client()
    
    try:
        result = client.token_renew()
        auth_data = result.get("auth", {})
        ttl = auth_data.get("lease_duration", 0)
        
        console.print("[green]✓[/green] 토큰 갱신 완료")
        console.print(f"  새 TTL: {format_duration(ttl)}")
        
    except VaultError as e:
        if "not renewable" in e.message.lower():
            console.print("[yellow]![/yellow] 이 토큰은 갱신할 수 없습니다.")
            
            # Try AppRole re-login
            if settings.has_approle_credentials():
                console.print("[dim]AppRole로 재인증 중...[/dim]")
                try:
                    result = client.approle_login(
                        settings.approle_role_id,
                        settings.approle_secret_id,
                        settings.approle_mount,
                    )
                    token = result.get("auth", {}).get("client_token")
                    if token:
                        settings.ensure_dirs()
                        settings.token_cache_file.write_text(token)
                        settings.token_cache_file.chmod(0o600)
                        console.print("[green]✓[/green] AppRole 재인증 성공")
                except VaultError as e2:
                    console.print(f"[red]✗[/red] 재인증 실패: {e2.message}")
                    raise typer.Exit(1)
        else:
            console.print(f"[red]✗[/red] 갱신 실패: {e.message}")
            raise typer.Exit(1)
