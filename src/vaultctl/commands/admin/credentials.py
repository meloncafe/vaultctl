"""Admin credentials command.
관리자 자격 증명 명령어.

Commands:
    vaultctl admin credentials        # Get Role ID + generate new Secret ID
"""

import socket
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Prompt

from vaultctl.config import settings
from vaultctl.utils import copy_to_clipboard
from vaultctl.vault_client import VaultClient, VaultError

console = Console()


def get_credentials(
    role: str = typer.Option("vaultctl", "--role", "-r", help="AppRole name"),
    ttl: Optional[str] = typer.Option(None, "--ttl", "-t", help="Secret ID TTL (e.g., 24h, 7d)"),
    copy_role: bool = typer.Option(False, "--copy-role", help="Copy Role ID to clipboard"),
    copy_secret: bool = typer.Option(False, "--copy-secret", help="Copy Secret ID to clipboard"),
):
    """Get Role ID and generate new Secret ID for existing AppRole.
    기존 AppRole의 Role ID 조회 및 새 Secret ID 생성.
    
    This command requires admin token to generate Secret IDs.
    """
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
    except VaultError:
        console.print(f"[red]✗[/red] AppRole not found: {role}")
        console.print("   Run 'vaultctl admin setup vault' to create it.")
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
    console.print(f"\n[yellow]{'─' * 60}[/yellow]")
    console.print("[bold yellow]AppRole Credentials[/bold yellow]")
    console.print(f"[yellow]{'─' * 60}[/yellow]")
    console.print(f"\n  Role ID:    {role_id}")
    console.print(f"  Secret ID:  {secret_id}  [dim](new)[/dim]")
    if ttl:
        console.print(f"  TTL:        {ttl}")
    console.print(f"\n  [dim]Accessor:  {secret_id_accessor}[/dim]")
    console.print(f"  [dim]Generated on: {hostname}[/dim]")
    console.print(f"\n[yellow]{'─' * 60}[/yellow]")
    
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
