"""vaultctl - Simple Vault CLI for LXC environments.
LXC 환경을 위한 간단한 Vault CLI.

Usage (User):
    vaultctl init              # Initial setup (one-time)
    vaultctl env <lxc-name>    # Generate .env file
    vaultctl status            # Check connection and auth status
    
    vaultctl run <n> -- cmd    # Run with injected env vars
    vaultctl sh <n>            # Generate shell export
    vaultctl watch <n> -- cmd  # Auto-restart on secret change
    
Usage (Admin):
    vaultctl admin ...         # Administrator commands
"""
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from vaultctl import __version__
from vaultctl.commands import admin, extended
from vaultctl.config import settings
from vaultctl.utils import format_duration, write_env_file
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(
    name="vaultctl",
    help="Simple Vault CLI for LXC environments / LXC 환경을 위한 간단한 Vault CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# Admin sub-command
app.add_typer(admin.app, name="admin", help="Administrator commands / 관리자 명령어")

# Extended commands (user-facing)
app.command("run")(extended.run_command)
app.command("sh")(extended.shell_export)
app.command("scan")(extended.scan_secrets)
app.command("redact")(extended.redact_secrets)
app.command("watch")(extended.watch_and_restart)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client / 인증된 클라이언트 반환."""
    client = VaultClient()
    
    # 1. Try cached token
    if settings.token_cache_file.exists():
        try:
            token = settings.token_cache_file.read_text().strip()
            if token:
                client = VaultClient(token=token)
                if client.is_authenticated():
                    return client
        except PermissionError:
            pass
    
    # 2. Try config token
    if settings.vault_token:
        client = VaultClient(token=settings.vault_token)
        if client.is_authenticated():
            return client
    
    # 3. Try AppRole auto-login
    if settings.has_approle_credentials():
        try:
            result = client.approle_login(
                settings.approle_role_id,
                settings.approle_secret_id,
                settings.approle_mount,
            )
            token = result.get("auth", {}).get("client_token")
            if token:
                # Cache the token
                try:
                    settings.ensure_dirs()
                    settings.token_cache_file.write_text(token)
                    settings.token_cache_file.chmod(0o600)
                except PermissionError:
                    pass
                return VaultClient(token=token)
        except VaultError:
            pass
    
    console.print("[red]✗[/red] Authentication required.")
    console.print("  Run: vaultctl init")
    raise typer.Exit(1)


def _get_secret_path(name: str) -> str:
    """Get KV secret path / 시크릿 경로 생성."""
    return f"{settings.kv_lxc_path}/{name}"


# ═══════════════════════════════════════════════════════════════════════════════
# Main Commands (for regular users)
# ═══════════════════════════════════════════════════════════════════════════════


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version / 버전 출력"),
):
    """Simple Vault CLI for LXC environments.
    
    \b
    Quick Start:
        vaultctl init              # Initial setup (one-time)
        vaultctl env lxc-000       # Generate .env file
        docker compose up -d       # Run
    
    \b
    Advanced:
        vaultctl run lxc-000 -- node app.js   # Run with injected env vars
        vaultctl watch lxc-000 -- docker compose up  # Auto-restart
        eval "$(vaultctl sh lxc-000)"         # Load shell env vars
    
    \b
    Administrator:
        vaultctl admin list        # List secrets
        vaultctl admin put lxc-000 DB_HOST=localhost
        vaultctl admin setup vault # Initial Vault setup
    """
    if version:
        console.print(f"vaultctl {__version__}")
        raise typer.Exit(0)


@app.command("init")
def init_command():
    """Initialize vaultctl (one-time setup) / 초기 설정 (한 번만).
    
    Configures Vault connection and AppRole authentication.
    Configuration saved to ~/.config/vaultctl/
    
    \b
    Examples:
        vaultctl init
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl Initial Setup[/bold blue]\n\n"
        "Configure Vault connection and authentication.\n"
        "This setup only needs to be done once.",
        title="🔐 Setup",
    ))
    console.print()
    
    # 1. Vault address - use existing if valid
    current_addr = settings.vault_addr
    has_valid_addr = current_addr and current_addr != "https://vault.example.com"
    
    if has_valid_addr:
        # Test existing connection
        console.print(f"[dim]Using configured Vault: {current_addr}[/dim]")
        client = VaultClient(addr=current_addr)
        health = client.health()
        
        if health.get("initialized") and not health.get("sealed"):
            console.print(f"[green]✓[/green] Connected to {current_addr}")
            vault_addr = current_addr
        else:
            # Ask for new address
            vault_addr = Prompt.ask("Vault server address", default=current_addr)
            client = VaultClient(addr=vault_addr)
            health = client.health()
            
            if not health.get("initialized"):
                console.print("[red]✗[/red] Cannot connect to Vault server.")
                raise typer.Exit(1)
            
            if health.get("sealed"):
                console.print("[red]✗[/red] Vault server is sealed.")
                raise typer.Exit(1)
            
            console.print("[green]✓[/green] Connection successful")
    else:
        # No valid address configured
        vault_addr = Prompt.ask("Vault server address")
        
        if not vault_addr:
            console.print("[red]✗[/red] Vault address is required.")
            raise typer.Exit(1)
        
        # Test connection
        console.print(f"\n[dim]Testing connection...[/dim]")
        client = VaultClient(addr=vault_addr)
        health = client.health()
        
        if not health.get("initialized"):
            console.print("[red]✗[/red] Cannot connect to Vault server.")
            raise typer.Exit(1)
        
        if health.get("sealed"):
            console.print("[red]✗[/red] Vault server is sealed.")
            raise typer.Exit(1)
        
        console.print("[green]✓[/green] Connection successful")
    
    # 2. AppRole credentials
    console.print("\n[bold]AppRole Authentication[/bold]")
    console.print("[dim]Get Role ID and Secret ID from your administrator.[/dim]")
    
    role_id = Prompt.ask("Role ID")
    secret_id = Prompt.ask("Secret ID", password=True)
    
    if not role_id or not secret_id:
        console.print("[red]✗[/red] Both Role ID and Secret ID are required.")
        raise typer.Exit(1)
    
    # 3. Test AppRole login
    console.print("\n[dim]Testing authentication...[/dim]")
    try:
        result = client.approle_login(role_id, secret_id, settings.approle_mount)
        token = result.get("auth", {}).get("client_token")
        
        if not token:
            console.print("[red]✗[/red] Authentication failed: no token received.")
            raise typer.Exit(1)
        
        console.print("[green]✓[/green] Authentication successful")
        
        auth_data = result.get("auth", {})
        console.print(f"  Policies: {', '.join(auth_data.get('policies', []))}")
        ttl = auth_data.get("lease_duration", 0)
        console.print(f"  TTL: {format_duration(ttl)}")
        
    except VaultError as e:
        console.print(f"[red]✗[/red] Authentication failed: {e.message}")
        raise typer.Exit(1)
    
    # 4. Save configuration
    console.print("\n[dim]Saving configuration...[/dim]")
    
    try:
        settings.ensure_dirs()
        
        # Save config (vault addr, role_id, secret_id)
        config_file = settings.config_dir / "config"
        config_file.write_text(f"""# vaultctl configuration
VAULT_ADDR={vault_addr}
VAULT_ROLE_ID={role_id}
VAULT_SECRET_ID={secret_id}
""")
        config_file.chmod(0o600)
        
        # Save token cache
        settings.token_cache_file.write_text(token)
        settings.token_cache_file.chmod(0o600)
        
        console.print(f"[green]✓[/green] Configuration saved: {settings.config_dir}/")
        
    except PermissionError as e:
        console.print(f"[yellow]![/yellow] Failed to save configuration: {e}")
        console.print("  Token is only kept in memory.")
    
    # 5. Done
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "You can now use these commands:\n"
        "  vaultctl env <lxc-name>    # Generate .env file\n"
        "  vaultctl status            # Check status\n"
        "  vaultctl run <n> -- cmd    # Run with injected env vars",
        title="✓ Complete",
    ))


@app.command("env")
def env_command(
    name: str = typer.Argument(..., help="Secret name (e.g., lxc-000) / 시크릿 이름"),
    output: Path = typer.Option(Path(".env"), "--output", "-o", help="Output file / 출력 파일"),
    stdout: bool = typer.Option(False, "--stdout", help="Output to stdout / stdout으로 출력"),
):
    """Generate .env file from Vault / Vault에서 .env 파일 생성.
    
    \b
    Examples:
        vaultctl env lxc-000              # Generate .env file
        vaultctl env lxc-000 -o prod.env  # Custom filename
        vaultctl env lxc-000 --stdout     # Output to stdout
        
        # Use with docker compose
        vaultctl env lxc-000 && docker compose up -d
    """
    client = _get_authenticated_client()
    
    try:
        data = client.kv_get(settings.kv_mount, _get_secret_path(name))
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] Secret not found: {name}")
            console.print(f"  Path: {settings.kv_mount}/{_get_secret_path(name)}")
            console.print("\n  Ask your administrator to create the secret:")
            console.print(f"    vaultctl admin put {name} KEY=value ...")
        else:
            console.print(f"[red]✗[/red] Failed to retrieve: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] Secret is empty: {name}")
        raise typer.Exit(1)

    if stdout:
        for key, value in sorted(data.items()):
            console.print(f"{key}={value}")
    else:
        write_env_file(str(output), data, header=f"Generated from Vault: {name}")
        console.print(f"[green]✓[/green] {output} ({len(data)} variables)")


@app.command("status")
def status_command():
    """Show connection and auth status / 연결 및 인증 상태 확인.
    
    \b
    Examples:
        vaultctl status
    """
    console.print("[bold]vaultctl Status[/bold]\n")
    
    # 1. Config
    console.print("1. Configuration")
    console.print(f"   Vault: {settings.vault_addr}")
    console.print(f"   KV Path: {settings.kv_mount}/{settings.kv_lxc_path}/")
    
    if settings.config_dir.exists():
        console.print(f"   Config Dir: [green]✓[/green] {settings.config_dir}")
    else:
        console.print(f"   Config Dir: [yellow]![/yellow] Not found")
    
    # 2. Connection
    console.print("\n2. Connection")
    client = VaultClient()
    health = client.health()
    
    if health.get("initialized") and not health.get("sealed"):
        console.print("   [green]✓[/green] Vault server connected")
    else:
        console.print("   [red]✗[/red] Vault server connection failed")
        raise typer.Exit(1)
    
    # 3. Authentication
    console.print("\n3. Authentication")
    
    try:
        client = _get_authenticated_client()
        token_info = client.token_lookup()
        data = token_info.get("data", {})
        
        console.print("   [green]✓[/green] Authenticated")
        console.print(f"   Policies: {', '.join(data.get('policies', []))}")
        
        ttl = data.get("ttl", 0)
        if ttl == 0:
            console.print("   TTL: [green]unlimited[/green]")
        else:
            remaining = format_duration(ttl)
            if ttl < settings.token_renew_threshold:
                console.print(f"   TTL: [yellow]{remaining}[/yellow] (renewal recommended)")
            else:
                console.print(f"   TTL: {remaining}")
                
    except typer.Exit:
        console.print("   [red]✗[/red] Authentication required")
        console.print("   Run: vaultctl init")
        raise
    
    # 4. Secrets access test
    console.print("\n4. Secrets Access")
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_lxc_path)
        console.print(f"   [green]✓[/green] Access granted ({len(items) if items else 0} secrets)")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    console.print("\n[green]✓[/green] All checks passed")


@app.command("config")
def config_command():
    """Show current configuration / 현재 설정 출력.
    
    \b
    Examples:
        vaultctl config
    """
    table = Table(title="Current Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="green")
    table.add_column("Value", style="white")

    configs = [
        ("Vault Address", settings.vault_addr),
        ("KV Mount", settings.kv_mount),
        ("Secret Path", settings.kv_lxc_path),
        ("AppRole Role ID", settings.approle_role_id[:8] + "..." if settings.approle_role_id else "-"),
        ("Config Directory", str(settings.config_dir)),
        ("Cache Directory", str(settings.cache_dir)),
    ]

    for name, value in configs:
        table.add_row(name, str(value) if value else "-")

    console.print(table)


if __name__ == "__main__":
    app()
