"""Admin token management commands.
관리자 토큰 관리 명령어.

Commands:
    vaultctl admin token status       # Token status
    vaultctl admin token renew        # Renew token
"""

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vaultctl.config import settings
from vaultctl.utils import format_duration
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(help="Token management / 토큰 관리")
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


@app.command("status")
def token_status():
    """Show token status / 토큰 상태 확인."""
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


@app.command("renew")
def token_renew():
    """Renew token / 토큰 갱신."""
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
