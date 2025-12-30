"""Admin commands for vaultctl.
관리자 전용 명령어.

Usage:
    vaultctl admin setup vault        # Create Vault policy and AppRole
    vaultctl admin setup apt-server   # Build APT repository server
    vaultctl admin setup apt-client   # Configure APT client
    
    vaultctl admin credentials        # Get Role ID + generate new Secret ID
    
    vaultctl admin list               # List secrets
    vaultctl admin get <n>            # Get secret
    vaultctl admin put <n> K=V        # Store secret
    vaultctl admin delete <n>         # Delete secret
    
    vaultctl admin token status       # Token status
    vaultctl admin token renew        # Renew token
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

# Repository sub-command (direct import to avoid circular import)
from vaultctl.commands.repo import app as repo_app
app.add_typer(repo_app, name="repo", help="APT package management / APT 패키지 관리")


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
    
    console.print("[red]✗[/red] Authentication required.")
    console.print("  Run: vaultctl init")
    raise typer.Exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# Secret Management Commands (list, get, put, delete)
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("list")
def list_secrets(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed info / 상세 정보 출력"),
):
    """List all secrets / 시크릿 목록 조회.
    
    Examples:
        vaultctl admin list
        vaultctl admin list -v
    """
    client = _get_authenticated_client()
    
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to list: {e.message}")
        console.print(f"  Path: {settings.kv_mount}/{settings.kv_path}/")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] No secrets found.")
        console.print(f"  Path: {settings.kv_mount}/{settings.kv_path}/")
        return

    table = Table(title="Secrets", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")

    if verbose:
        table.add_column("Keys", style="white")
        table.add_column("Key List", style="dim")

        for item in sorted(items):
            name = item.rstrip("/")
            try:
                secret_path = settings.get_secret_path(name)
                data = client.kv_get(settings.kv_mount, secret_path)
                keys = ", ".join(sorted(data.keys()))
                if len(keys) > 50:
                    keys = keys[:50] + "..."
                table.add_row(name, str(len(data)), keys)
            except VaultError:
                table.add_row(name, "-", "[red]failed[/red]")
    else:
        for item in sorted(items):
            table.add_row(item.rstrip("/"))

    console.print(table)
    console.print(f"\nTotal: {len(items)}")
    console.print(f"[dim]Path: {settings.kv_mount}/{settings.kv_path}/[/dim]")


@app.command("get")
def get_secret(
    name: str = typer.Argument(..., help="Secret name (e.g., 100) / 시크릿 이름"),
    field: Optional[str] = typer.Option(None, "--field", "-f", help="Specific field only / 특정 필드만 조회"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy to clipboard / 클립보드에 복사"),
    raw: bool = typer.Option(False, "--raw", help="JSON output / JSON으로 출력"),
):
    """Get secret / 시크릿 조회.
    
    Examples:
        vaultctl admin get 100
        vaultctl admin get 100 -f DB_PASSWORD
        vaultctl admin get 100 -f DB_PASSWORD --copy
        vaultctl admin get 100 --raw
    """
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    try:
        data = client.kv_get(settings.kv_mount, secret_path)
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] Secret not found: {name}")
            console.print(f"  Path: {settings.kv_mount}/{secret_path}")
        else:
            console.print(f"[red]✗[/red] Failed to retrieve: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] Secret is empty: {name}")
        raise typer.Exit(1)

    # Specific field only
    if field:
        if field not in data:
            console.print(f"[red]✗[/red] Field not found: {field}")
            console.print(f"  Available: {', '.join(data.keys())}")
            raise typer.Exit(1)

        value = str(data[field])

        if copy:
            if copy_to_clipboard(value):
                console.print(f"[green]✓[/green] Copied to clipboard: {name}/{field}")
            else:
                console.print(f"[yellow]![/yellow] Clipboard copy failed")
                console.print(value)
        elif raw:
            console.print(value)
        else:
            console.print(f"[bold]{field}[/bold]: {value}")
        return

    # Full output
    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        table = create_kv_table(data, title=f"Secret: {name}")
        console.print(table)


@app.command("put")
def put_secret(
    name: str = typer.Argument(..., help="Secret name (e.g., 100) / 시크릿 이름"),
    data: list[str] = typer.Argument(..., help="KEY=value pairs / KEY=value 쌍들"),
    merge: bool = typer.Option(True, "--merge/--replace", help="Merge with existing (default) / Replace all / 기존 값과 병합 또는 교체"),
):
    """Store secret / 시크릿 저장.
    
    Examples:
        vaultctl admin put 100 DB_HOST=postgres.local DB_PASSWORD=secret
        vaultctl admin put 100 NEW_KEY=value --merge
        vaultctl admin put 100 ONLY_THIS=value --replace
    """
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    new_data = parse_key_value_args(data)
    if not new_data:
        console.print("[red]✗[/red] Provide data in KEY=value format.")
        console.print("  Example: vaultctl admin put 100 DB_HOST=localhost DB_PASSWORD=secret")
        raise typer.Exit(1)

    # Merge with existing
    if merge:
        try:
            existing = client.kv_get(settings.kv_mount, secret_path)
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass  # Create new

    try:
        client.kv_put(settings.kv_mount, secret_path, new_data)
        console.print(f"[green]✓[/green] Saved: {name}")
        console.print(f"[dim]Path: {settings.kv_mount}/{secret_path}[/dim]")

        # Show saved content
        table = create_kv_table(new_data, title=f"Secret: {name}")
        console.print(table)

    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to save: {e.message}")
        raise typer.Exit(1)


@app.command("delete")
def delete_secret(
    name: str = typer.Argument(..., help="Secret name / 시크릿 이름"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation / 확인 없이 삭제"),
):
    """Delete secret / 시크릿 삭제.
    
    Examples:
        vaultctl admin delete 100
        vaultctl admin delete 100 --force
    """
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    if not force:
        confirm = typer.confirm(f"Delete '{name}'?")
        if not confirm:
            console.print("Cancelled")
            raise typer.Exit(0)

    try:
        client.kv_delete(settings.kv_mount, secret_path)
        console.print(f"[green]✓[/green] Deleted: {name}")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to delete: {e.message}")
        raise typer.Exit(1)


@app.command("import")
def import_secrets(
    file: Path = typer.Argument(..., help="JSON file path / JSON 파일 경로"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate only, no save / 실제 저장 없이 검증만"),
):
    """Import secrets from JSON file / JSON 파일에서 시크릿 일괄 등록.
    
    JSON format:
        {
            "100": {"DB_HOST": "localhost", "DB_PASSWORD": "secret"},
            "101": {"REDIS_URL": "redis://localhost:6379"}
        }
    
    Examples:
        vaultctl admin import secrets.json
        vaultctl admin import secrets.json --dry-run
    """
    client = _get_authenticated_client()
    
    if not file.exists():
        console.print(f"[red]✗[/red] File not found: {file}")
        raise typer.Exit(1)

    try:
        with open(file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] JSON parse error: {e}")
        raise typer.Exit(1)

    # Remove meta fields like _description
    data = {k: v for k, v in data.items() if not k.startswith("_")}

    if not data:
        console.print("[yellow]![/yellow] No secrets to import.")
        return

    console.print(f"[dim]Importing {len(data)} secrets {'(dry-run)' if dry_run else ''}...[/dim]")

    success = 0
    failed = 0

    for name, secret_data in data.items():
        if not isinstance(secret_data, dict):
            console.print(f"  [red]✗[/red] {name}: invalid format")
            failed += 1
            continue

        # Remove empty values
        secret_data = {k: v for k, v in secret_data.items() if v}

        if dry_run:
            console.print(f"  [dim]○[/dim] {name}: {len(secret_data)} fields")
            success += 1
        else:
            try:
                secret_path = settings.get_secret_path(name)
                client.kv_put(settings.kv_mount, secret_path, secret_data)
                console.print(f"  [green]✓[/green] {name}")
                success += 1
            except VaultError as e:
                console.print(f"  [red]✗[/red] {name}: {e.message}")
                failed += 1

    console.print(f"\nComplete: {success} succeeded, {failed} failed")


@app.command("export")
def export_secrets(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (stdout if omitted) / 출력 파일 (생략 시 stdout)"),
):
    """Export all secrets to JSON / 모든 시크릿을 JSON으로 내보내기.
    
    Examples:
        vaultctl admin export
        vaultctl admin export -o secrets.json
    """
    client = _get_authenticated_client()
    
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_path)
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to list: {e.message}")
        raise typer.Exit(1)

    if not items:
        console.print("[yellow]![/yellow] No secrets found.")
        return

    result = {}
    for item in items:
        name = item.rstrip("/")
        try:
            secret_path = settings.get_secret_path(name)
            data = client.kv_get(settings.kv_mount, secret_path)
            result[name] = data
        except VaultError:
            result[name] = {}

    json_output = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        output.write_text(json_output)
        console.print(f"[green]✓[/green] Exported: {output}")
    else:
        console.print(json_output)


# ═══════════════════════════════════════════════════════════════════════════════
# Setup Commands
# ═══════════════════════════════════════════════════════════════════════════════


@setup_app.command("vault")
def setup_vault(
    generate_secret: bool = typer.Option(False, "--generate-secret", "-g", help="Generate new Secret ID for existing AppRole / 기존 AppRole에 새 Secret ID 생성"),
):
    """Setup Vault policy and AppRole / Vault 정책 및 AppRole 생성.
    
    Creates:
        - Policy: vaultctl (read/write to kv/<path>/*)
        - AppRole: vaultctl
    
    Use --generate-secret to create a new Secret ID for an existing AppRole.
    
    Examples:
        vaultctl admin setup vault                # Full setup
        vaultctl admin setup vault -g             # Generate new Secret ID only
    """
    # Get admin token
    vault_addr = Prompt.ask(
        "Vault server address",
        default=settings.vault_addr if settings.vault_addr != "https://vault.example.com" else None,
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
    
    role_name = "vaultctl"
    policy_name = "vaultctl"
    
    # Generate Secret ID only mode
    if generate_secret:
        console.print("\n[bold]Generating new Secret ID...[/bold]")
        try:
            # Check if AppRole exists
            client._request("GET", f"auth/approle/role/{role_name}")
            
            # Get Role ID
            role_id_resp = client._request("GET", f"auth/approle/role/{role_name}/role-id")
            role_id = role_id_resp.get("data", {}).get("role_id")
            
            # Generate new Secret ID
            secret_id_resp = client._request("POST", f"auth/approle/role/{role_name}/secret-id")
            secret_id = secret_id_resp.get("data", {}).get("secret_id")
            
            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            console.print("[yellow]Save these credentials securely![/yellow]")
            console.print(f"[yellow]{'─' * 60}[/yellow]")
            console.print(f"\n  Role ID:    {role_id}")
            console.print(f"  Secret ID:  {secret_id}  [dim](newly generated)[/dim]")
            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            return
            
        except VaultError as e:
            console.print(f"[red]✗[/red] AppRole '{role_name}' not found. Run without -g to create it.")
            raise typer.Exit(1)
    
    # Full setup - get path configuration
    console.print("\n[bold]KV Path Configuration[/bold]")
    console.print("[dim]This determines where secrets are stored and what the policy allows.[/dim]")
    
    kv_mount = Prompt.ask("KV engine mount", default="kv")
    kv_path = Prompt.ask("Secret base path", default="proxmox/lxc")
    
    # Remove trailing slashes
    kv_path = kv_path.strip("/")
    
    console.print(Panel.fit(
        "[bold blue]Vault Setup[/bold blue]\n\n"
        "This will create:\n"
        f"• Policy: {policy_name}\n"
        f"• AppRole: {role_name}\n"
        f"• Access: {kv_mount}/data/{kv_path}/*",
        title="🔐 Vault Setup",
    ))
    
    # 1. Check KV secrets engine
    console.print("\n[bold]1. KV Secrets Engine[/bold]")
    
    try:
        mounts = client._request("GET", "sys/mounts")
        if f"{kv_mount}/" in mounts.get("data", {}):
            console.print(f"   [green]✓[/green] Exists: {kv_mount}/")
        else:
            console.print(f"   [yellow]![/yellow] KV engine '{kv_mount}' not found.")
            console.print(f"   Enable it with: vault secrets enable -path={kv_mount} kv-v2")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    # 2. Create policy with correct paths
    console.print("\n[bold]2. Policy[/bold]")
    
    # Extract the first path component for policy (e.g., "proxmox" from "proxmox/lxc")
    policy_path = kv_path.split("/")[0] if "/" in kv_path else kv_path
    
    policy_hcl = f'''# vaultctl policy
# Read/write access to {kv_mount}/{policy_path}/*

path "{kv_mount}/data/{policy_path}/*" {{
  capabilities = ["create", "read", "update", "delete", "list"]
}}

path "{kv_mount}/metadata/{policy_path}/*" {{
  capabilities = ["list", "read", "delete"]
}}

path "auth/token/lookup-self" {{
  capabilities = ["read"]
}}

path "auth/token/renew-self" {{
  capabilities = ["update"]
}}
'''
    
    try:
        client._request("PUT", f"sys/policies/acl/{policy_name}", data={
            "policy": policy_hcl,
        })
        console.print(f"   [green]✓[/green] Created: {policy_name}")
        console.print(f"   [dim]Access: {kv_mount}/data/{policy_path}/*[/dim]")
    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)
    
    # 3. Enable AppRole auth
    console.print("\n[bold]3. AppRole Auth[/bold]")
    try:
        auth_methods = client._request("GET", "sys/auth")
        if "approle/" in auth_methods.get("data", {}):
            console.print("   [green]✓[/green] Already enabled: approle/")
        else:
            client._request("POST", "sys/auth/approle", data={
                "type": "approle",
            })
            console.print("   [green]✓[/green] Enabled: approle/")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    # 4. Create or update AppRole
    console.print("\n[bold]4. AppRole[/bold]")
    
    approle_exists = False
    try:
        client._request("GET", f"auth/approle/role/{role_name}")
        approle_exists = True
        console.print(f"   [green]✓[/green] Already exists: {role_name}")
    except VaultError:
        pass
    
    if not approle_exists:
        try:
            client._request("POST", f"auth/approle/role/{role_name}", data={
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
    
    # 5. Get Role ID and generate Secret ID
    console.print("\n[bold]5. Credentials[/bold]")
    try:
        role_id_resp = client._request("GET", f"auth/approle/role/{role_name}/role-id")
        role_id = role_id_resp.get("data", {}).get("role_id")
        
        secret_id_resp = client._request("POST", f"auth/approle/role/{role_name}/secret-id")
        secret_id = secret_id_resp.get("data", {}).get("secret_id")
        
        console.print(f"\n[yellow]{'─' * 60}[/yellow]")
        console.print("[yellow]Save these credentials securely![/yellow]")
        console.print(f"[yellow]{'─' * 60}[/yellow]")
        console.print(f"\n  Role ID:    {role_id}")
        console.print(f"  Secret ID:  {secret_id}")
        console.print(f"\n  KV Mount:   {kv_mount}")
        console.print(f"  KV Path:    {kv_path}")
        console.print(f"\n[yellow]{'─' * 60}[/yellow]")
        
    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)
    
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Distribute to each LXC:\n"
        f"  vaultctl init\n\n"
        "When prompted, enter:\n"
        f"  KV Mount: {kv_mount}\n"
        f"  KV Path:  {kv_path}\n\n"
        "Or to generate a new Secret ID:\n"
        f"  vaultctl admin setup vault -g",
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
        table.add_row("TTL", "[green]unlimited[/green]")
    else:
        remaining = format_duration(ttl)
        if ttl < settings.token_renew_threshold:
            table.add_row("TTL", f"[yellow]{remaining}[/yellow] (renewal recommended)")
        else:
            table.add_row("TTL", remaining)

    table.add_row("Renewable", "Yes" if data.get("renewable", False) else "No")
    
    creation_time = data.get("creation_time", "-")
    if isinstance(creation_time, (int, float)):
        from datetime import datetime
        creation_time = datetime.fromtimestamp(creation_time).strftime("%Y-%m-%d %H:%M:%S")
    table.add_row("Creation Time", str(creation_time))

    console.print(Panel(table, title="Token Info", border_style="blue"))


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
        
        console.print("[green]✓[/green] Token renewed")
        console.print(f"  New TTL: {format_duration(ttl)}")
        
    except VaultError as e:
        if "not renewable" in e.message.lower():
            console.print("[yellow]![/yellow] This token is not renewable.")
            
            # Try AppRole re-login
            if settings.has_approle_credentials():
                console.print("[dim]Re-authenticating with AppRole...[/dim]")
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
                        console.print("[green]✓[/green] AppRole re-authentication successful")
                except VaultError as e2:
                    console.print(f"[red]✗[/red] Re-authentication failed: {e2.message}")
                    raise typer.Exit(1)
        else:
            console.print(f"[red]✗[/red] Renewal failed: {e.message}")
            raise typer.Exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# Credentials Command (for existing AppRole)
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("credentials")
def get_credentials(
    role: str = typer.Option("vaultctl", "--role", "-r", help="AppRole name / AppRole 이름"),
    ttl: Optional[str] = typer.Option(None, "--ttl", "-t", help="Secret ID TTL (e.g., 24h, 7d) / Secret ID 유효기간"),
    copy_role: bool = typer.Option(False, "--copy-role", help="Copy Role ID to clipboard / Role ID 클립보드 복사"),
    copy_secret: bool = typer.Option(False, "--copy-secret", help="Copy Secret ID to clipboard / Secret ID 클립보드 복사"),
):
    """Get Role ID and generate new Secret ID for existing AppRole.
    기존 AppRole의 Role ID 조회 및 새 Secret ID 생성.
    
    This command requires admin token to generate Secret IDs.
    Use this to get credentials for new LXC containers.
    이 명령어는 Secret ID 생성을 위해 admin 토큰이 필요합니다.
    새 LXC 컨테이너에 자격 증명을 배포할 때 사용하세요.
    
    \b
    Examples:
        vaultctl admin credentials              # Get credentials for vaultctl role
        vaultctl admin credentials -r myapp     # For different role
        vaultctl admin credentials --ttl 7d     # With 7-day TTL
        vaultctl admin credentials --copy-secret  # Copy Secret ID
    """
    import socket
    
    # Get admin token
    vault_addr = Prompt.ask(
        "Vault server address",
        default=settings.vault_addr if settings.vault_addr != "https://vault.example.com" else None,
    )
    admin_token = Prompt.ask("Root/Admin token", password=True)
    
    client = VaultClient(addr=vault_addr, token=admin_token)
    
    # Test connection
    console.print("\n[dim]Connecting...[/dim]")
    try:
        client.token_lookup()
        console.print("[green]✓[/green] Connected")
    except VaultError as e:
        console.print(f"[red]✗[/red] Connection failed: {e.message}")
        raise typer.Exit(1)
    
    # Check if AppRole exists
    console.print(f"\n[dim]Checking AppRole: {role}...[/dim]")
    try:
        role_info = client.approle_read_role(role)
        policies = role_info.get("token_policies", [])
        console.print(f"[green]✓[/green] AppRole found: {role}")
        console.print(f"   [dim]Policies: {', '.join(policies)}[/dim]")
    except VaultError as e:
        console.print(f"[red]✗[/red] AppRole not found: {role}")
        console.print(f"   Run 'vaultctl admin setup vault' to create it.")
        raise typer.Exit(1)
    
    # Get Role ID
    try:
        role_id = client.approle_get_role_id(role)
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to get Role ID: {e.message}")
        raise typer.Exit(1)
    
    # Generate new Secret ID with metadata
    hostname = socket.gethostname()
    metadata = {
        "generated_by": "vaultctl",
        "hostname": hostname,
    }
    
    try:
        secret_data = client.approle_generate_secret_id(
            role_name=role,
            metadata=metadata,
            ttl=ttl,
        )
        secret_id = secret_data.get("secret_id", "")
        secret_id_accessor = secret_data.get("secret_id_accessor", "")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to generate Secret ID: {e.message}")
        raise typer.Exit(1)
    
    # Output
    console.print(f"\n[yellow]{'\u2500' * 60}[/yellow]")
    console.print("[bold yellow]AppRole Credentials[/bold yellow]")
    console.print(f"[yellow]{'\u2500' * 60}[/yellow]")
    console.print(f"\n  Role ID:    {role_id}")
    console.print(f"  Secret ID:  {secret_id}  [dim](new)[/dim]")
    if ttl:
        console.print(f"  TTL:        {ttl}")
    console.print(f"\n  [dim]Accessor:  {secret_id_accessor}[/dim]")
    console.print(f"  [dim]Generated on: {hostname}[/dim]")
    console.print(f"\n[yellow]{'\u2500' * 60}[/yellow]")
    
    # Copy to clipboard
    if copy_role:
        if copy_to_clipboard(role_id):
            console.print("\n[green]✓[/green] Role ID copied to clipboard")
        else:
            console.print("\n[yellow]![/yellow] Failed to copy to clipboard")
    elif copy_secret:
        if copy_to_clipboard(secret_id):
            console.print("\n[green]✓[/green] Secret ID copied to clipboard")
        else:
            console.print("\n[yellow]![/yellow] Failed to copy to clipboard")
    
    # Usage hint
    console.print("\n[bold]Usage in LXC:[/bold]")
    console.print("  vaultctl init")
    console.print("  [dim]# Enter these credentials when prompted[/dim]")
