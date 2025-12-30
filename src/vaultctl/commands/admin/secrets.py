"""Admin secret management commands.
관리자 시크릿 관리 명령어.

Commands:
    vaultctl admin list               # List secrets
    vaultctl admin get <n>            # Get secret
    vaultctl admin put <n> K=V        # Store secret
    vaultctl admin delete <n>         # Delete secret
    vaultctl admin import <file>      # Import from JSON
    vaultctl admin export             # Export to JSON
"""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from vaultctl.config import settings
from vaultctl.utils import copy_to_clipboard, create_kv_table, parse_key_value_args
from vaultctl.vault_client import VaultClient, VaultError

console = Console()


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client."""
    client = VaultClient()
    
    if settings.token_cache_file.exists():
        try:
            token = settings.token_cache_file.read_text().strip()
            if token:
                client = VaultClient(token=token)
                if client.is_authenticated():
                    return client
        except PermissionError:
            pass
    
    if settings.vault_token:
        client = VaultClient(token=settings.vault_token)
        if client.is_authenticated():
            return client
    
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


def list_secrets(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed info"),
):
    """List all secrets / 시크릿 목록 조회."""
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


def get_secret(
    name: str = typer.Argument(..., help="Secret name (e.g., 001)"),
    field: Optional[str] = typer.Option(None, "--field", "-f", help="Specific field only"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy to clipboard"),
    raw: bool = typer.Option(False, "--raw", help="JSON output"),
):
    """Get secret / 시크릿 조회."""
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

    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        table = create_kv_table(data, title=f"Secret: {name}")
        console.print(table)


def put_secret(
    name: str = typer.Argument(..., help="Secret name (e.g., 001)"),
    data: list[str] = typer.Argument(..., help="KEY=value pairs"),
    merge: bool = typer.Option(True, "--merge/--replace", help="Merge with existing (default)"),
):
    """Store secret / 시크릿 저장."""
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    new_data = parse_key_value_args(data)
    if not new_data:
        console.print("[red]✗[/red] Provide data in KEY=value format.")
        console.print("  Example: vaultctl admin put 001 DB_HOST=localhost DB_PASSWORD=secret")
        raise typer.Exit(1)

    if merge:
        try:
            existing = client.kv_get(settings.kv_mount, secret_path)
            existing.update(new_data)
            new_data = existing
        except VaultError:
            pass

    try:
        client.kv_put(settings.kv_mount, secret_path, new_data)
        console.print(f"[green]✓[/green] Saved: {name}")
        console.print(f"[dim]Path: {settings.kv_mount}/{secret_path}[/dim]")

        table = create_kv_table(new_data, title=f"Secret: {name}")
        console.print(table)

    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to save: {e.message}")
        raise typer.Exit(1)


def delete_secret(
    name: str = typer.Argument(..., help="Secret name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete secret / 시크릿 삭제."""
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


def import_secrets(
    file: Path = typer.Argument(..., help="JSON file path"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate only, no save"),
):
    """Import secrets from JSON file / JSON 파일에서 시크릿 일괄 등록."""
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


def export_secrets(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (stdout if omitted)"),
):
    """Export all secrets to JSON / 모든 시크릿을 JSON으로 내보내기."""
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
