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
import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from ruamel.yaml import YAML

from vaultctl import __version__
from vaultctl.commands.admin import app as admin_app
from vaultctl.commands.user.compose import app as compose_app
from vaultctl.commands.user import extended
from vaultctl.config import settings
from vaultctl.utils import format_duration, write_env_file, load_env_file
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(
    name="vaultctl",
    help="Simple Vault CLI for LXC environments / LXC 환경을 위한 간단한 Vault CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

# Admin sub-command
app.add_typer(admin_app, name="admin", help="Administrator commands / 관리자 명령어")

# Compose sub-command
app.add_typer(compose_app, name="compose", help="Docker Compose integration / Docker Compose 통합")

# Extended commands (user-facing)
app.command("run")(extended.run_command)
app.command("sh")(extended.shell_export)
app.command("scan")(extended.scan_secrets)
app.command("redact")(extended.redact_secrets)
app.command("watch")(extended.watch_and_restart)


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


def _find_compose_file() -> Optional[Path]:
    """Find docker-compose.yml in current directory."""
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        path = Path(name)
        if path.exists():
            return path
    return None


def _find_env_file() -> Optional[Path]:
    """Find .env file in current directory."""
    path = Path(".env")
    return path if path.exists() else None


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
):
    """Simple Vault CLI for LXC environments.
    
    \b
    Quick Start:
        vaultctl init              # Initial setup (one-time)
        vaultctl env 100           # Generate .env file
        docker compose up -d       # Run
    
    \b
    Docker Compose:
        vaultctl compose init 100            # Setup compose + secrets
        vaultctl compose up 100              # Sync secrets & start
        vaultctl compose restart 100         # Sync & restart
    
    \b
    Advanced:
        vaultctl run 100 -- node app.js       # Run with injected env vars
        vaultctl watch 100 -- docker compose up  # Auto-restart
    
    \b
    Administrator:
        vaultctl admin list        # List secrets
        vaultctl admin put 100 DB_HOST=localhost
        vaultctl admin setup vault # Initial Vault setup
    """
    if version:
        console.print(f"vaultctl {__version__}")
        raise typer.Exit(0)


@app.command("init")
def init_command(
    role_name: str = typer.Option("vaultctl", "--role", "-r", help="AppRole name"),
    secret_name: Optional[str] = typer.Option(None, "--name", "-n", help="Secret name to create/use"),
):
    """Initialize vaultctl (one-time setup).
    
    This command will:
    1. Connect to Vault server
    2. Generate AppRole credentials for this machine
    3. If .env exists: upload to Vault and create .env.secrets
    4. If docker-compose.yml exists: optionally configure it
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl Initial Setup[/bold blue]\n\n"
        "Connect to Vault and configure this environment.",
        title="🔐 Setup",
    ))
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: Vault Connection
    # ─────────────────────────────────────────────────────────────────────────
    current_addr = settings.vault_addr
    has_valid_addr = current_addr and current_addr != "https://vault.example.com"
    
    vault_addr = Prompt.ask(
        "Vault server address",
        default=current_addr if has_valid_addr else None,
    )
    
    if not vault_addr:
        console.print("[red]✗[/red] Vault address is required.")
        raise typer.Exit(1)
    
    console.print(f"\n[dim]Connecting to {vault_addr}...[/dim]")
    client = VaultClient(addr=vault_addr)
    health = client.health()
    
    if not health.get("initialized") or health.get("sealed"):
        console.print("[red]✗[/red] Cannot connect to Vault server.")
        raise typer.Exit(1)
    
    console.print("[green]✓[/green] Connection successful")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: Admin Authentication
    # ─────────────────────────────────────────────────────────────────────────
    console.print("\n[bold]Admin Authentication[/bold]")
    console.print("[dim]Admin token is required to generate credentials.[/dim]")
    admin_token = Prompt.ask("Admin/Root token", password=True)
    
    if not admin_token:
        console.print("[red]✗[/red] Admin token is required.")
        raise typer.Exit(1)
    
    admin_client = VaultClient(addr=vault_addr, token=admin_token)
    try:
        admin_client.token_lookup()
        console.print("[green]✓[/green] Admin authentication successful")
    except VaultError as e:
        console.print(f"[red]✗[/red] Admin authentication failed: {e.message}")
        raise typer.Exit(1)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: KV Path Settings
    # ─────────────────────────────────────────────────────────────────────────
    console.print("\n[bold]KV Secret Path[/bold]")
    kv_mount = Prompt.ask("KV engine mount", default=settings.kv_mount or "kv")
    kv_path = Prompt.ask("Secret base path", default=settings.kv_path or "proxmox/lxc")
    kv_path = kv_path.strip("/")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: AppRole Setup
    # ─────────────────────────────────────────────────────────────────────────
    role_name = Prompt.ask("AppRole name", default=role_name)
    
    console.print(f"\n[dim]Checking AppRole '{role_name}'...[/dim]")
    try:
        admin_client.approle_read_role(role_name)
        console.print(f"[green]✓[/green] AppRole found: {role_name}")
    except VaultError:
        console.print(f"[red]✗[/red] AppRole '{role_name}' not found.")
        console.print("\n  First run on admin workstation:")
        console.print("    vaultctl admin setup vault")
        raise typer.Exit(1)
    
    try:
        role_id = admin_client.approle_get_role_id(role_name)
        console.print(f"[green]✓[/green] Role ID retrieved")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to get Role ID: {e.message}")
        raise typer.Exit(1)
    
    hostname = socket.gethostname()
    console.print(f"\n[dim]Generating Secret ID for {hostname}...[/dim]")
    
    try:
        secret_data = admin_client.approle_generate_secret_id(
            role_name=role_name,
            metadata={"generated_by": "vaultctl init", "hostname": hostname},
        )
        secret_id = secret_data.get("secret_id", "")
        console.print(f"[green]✓[/green] Secret ID generated")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to generate Secret ID: {e.message}")
        raise typer.Exit(1)
    
    # Test AppRole login
    console.print("\n[dim]Testing AppRole authentication...[/dim]")
    try:
        result = client.approle_login(role_id, secret_id, settings.approle_mount)
        token = result.get("auth", {}).get("client_token")
        
        if not token:
            console.print("[red]✗[/red] Authentication failed: no token received.")
            raise typer.Exit(1)
        
        console.print("[green]✓[/green] AppRole authentication successful")
    except VaultError as e:
        console.print(f"[red]✗[/red] AppRole authentication failed: {e.message}")
        raise typer.Exit(1)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 5: Save Configuration
    # ─────────────────────────────────────────────────────────────────────────
    console.print("\n[dim]Saving configuration...[/dim]")
    try:
        settings.ensure_dirs()
        
        config_file = settings.config_dir / "config"
        config_file.write_text(f"""# vaultctl configuration
VAULT_ADDR={vault_addr}
VAULT_KV_MOUNT={kv_mount}
VAULT_KV_PATH={kv_path}
VAULT_ROLE_ID={role_id}
VAULT_SECRET_ID={secret_id}
""")
        config_file.chmod(0o600)
        
        settings.token_cache_file.write_text(token)
        settings.token_cache_file.chmod(0o600)
        
        # Reload settings
        settings.vault_addr = vault_addr
        settings.kv_mount = kv_mount
        settings.kv_path = kv_path
        
        console.print(f"[green]✓[/green] Configuration saved: {settings.config_dir}/")
    except PermissionError as e:
        console.print(f"[yellow]![/yellow] Failed to save configuration: {e}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 6: Secret Name & .env Upload
    # ─────────────────────────────────────────────────────────────────────────
    console.print("\n[bold]Secret Configuration[/bold]")
    
    # Check for existing .env file
    env_file = _find_env_file()
    env_data: dict = {}
    
    if env_file:
        console.print(f"[blue]![/blue] Found .env file in current directory")
        env_data = load_env_file(str(env_file))
        console.print(f"   Contains {len(env_data)} variables")
    
    # Ask for secret name
    if not secret_name:
        secret_name = Prompt.ask(
            "Secret name (e.g., 163, myapp)",
            default=hostname.split("-")[-1] if "-" in hostname else hostname,
        )
    
    if not secret_name:
        console.print("[yellow]![/yellow] Skipping secret creation.")
    else:
        secret_full_path = f"{kv_path}/{secret_name}"
        
        # Check if secret already exists
        test_client = VaultClient(addr=vault_addr, token=token)
        try:
            existing_data = test_client.kv_get(kv_mount, secret_full_path)
            if existing_data:
                console.print(f"[blue]![/blue] Secret '{secret_name}' already exists ({len(existing_data)} vars)")
                if env_data:
                    if Confirm.ask("Merge .env into existing secret?", default=False):
                        merged = {**existing_data, **env_data}
                        test_client.kv_put(kv_mount, secret_full_path, merged)
                        console.print(f"[green]✓[/green] Merged {len(env_data)} vars into secret")
                        env_data = merged
                    else:
                        env_data = existing_data
                else:
                    env_data = existing_data
            else:
                raise VaultError("empty", 404)
        except VaultError:
            # Secret doesn't exist, create it
            if env_data:
                console.print(f"[dim]Creating secret '{secret_name}' from .env...[/dim]")
                test_client.kv_put(kv_mount, secret_full_path, env_data)
                console.print(f"[green]✓[/green] Created secret '{secret_name}' ({len(env_data)} vars)")
            else:
                # Create empty secret
                if Confirm.ask(f"Create empty secret '{secret_name}'?", default=True):
                    test_client.kv_put(kv_mount, secret_full_path, {"_placeholder": "true"})
                    console.print(f"[green]✓[/green] Created empty secret '{secret_name}'")
                    console.print("   Add secrets later: vaultctl admin put {secret_name} KEY=value")
        
        # ─────────────────────────────────────────────────────────────────────
        # Step 7: Generate .env.secrets
        # ─────────────────────────────────────────────────────────────────────
        if env_data:
            env_secrets_file = Path(".env.secrets")
            
            # Transform keys to UPPER_CASE
            transformed = {}
            for key, value in env_data.items():
                if key.startswith("_"):
                    continue  # Skip placeholder
                new_key = key.replace("-", "_").replace(".", "_").replace(" ", "_").upper()
                transformed[new_key] = value
            
            if transformed:
                write_env_file(str(env_secrets_file), transformed, header=f"Generated from Vault: {secret_name}")
                try:
                    env_secrets_file.chmod(0o600)
                except OSError:
                    pass
                console.print(f"[green]✓[/green] Created .env.secrets ({len(transformed)} vars)")
        
        # ─────────────────────────────────────────────────────────────────────
        # Step 8: Docker Compose Integration
        # ─────────────────────────────────────────────────────────────────────
        compose_file = _find_compose_file()
        
        if compose_file:
            console.print(f"\n[blue]![/blue] Found {compose_file}")
            
            if Confirm.ask("Configure docker-compose.yml to use .env.secrets?", default=True):
                # Read compose file
                with open(compose_file) as f:
                    compose_data = _yaml.load(f)
                
                services = compose_data.get("services", {})
                if not services:
                    console.print("[yellow]![/yellow] No services found in compose file")
                else:
                    # Backup
                    backup_file = compose_file.with_suffix(f".yml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                    shutil.copy(compose_file, backup_file)
                    console.print(f"[dim]Backup: {backup_file}[/dim]")
                    
                    # Update services
                    updated_count = 0
                    for svc_name, svc_config in services.items():
                        env_file_list = svc_config.get("env_file", [])
                        if isinstance(env_file_list, str):
                            env_file_list = [env_file_list]
                        
                        if ".env.secrets" not in env_file_list:
                            env_file_list.append(".env.secrets")
                            svc_config["env_file"] = env_file_list
                            updated_count += 1
                    
                    if updated_count > 0:
                        with open(compose_file, "w") as f:
                            _yaml.dump(compose_data, f)
                        console.print(f"[green]✓[/green] Updated {updated_count} services in {compose_file}")
                    else:
                        console.print("[dim]All services already configured[/dim]")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Done!
    # ─────────────────────────────────────────────────────────────────────────
    console.print("\n")
    
    usage_lines = [
        f"[bold green]Setup Complete![/bold green]\n",
        f"Vault: {vault_addr}",
        f"KV Path: {kv_mount}/{kv_path}/",
    ]
    
    if secret_name:
        usage_lines.append(f"Secret: {secret_name}")
        usage_lines.append("")
        usage_lines.append("Commands:")
        usage_lines.append(f"  vaultctl env {secret_name}        # Regenerate .env.secrets")
        usage_lines.append(f"  vaultctl compose up {secret_name}  # Sync & start containers")
    else:
        usage_lines.append("")
        usage_lines.append("Commands:")
        usage_lines.append("  vaultctl env <name>    # Generate .env.secrets")
        usage_lines.append("  vaultctl status        # Check status")
    
    console.print(Panel.fit("\n".join(usage_lines), title="✓ Complete"))


@app.command("env")
def env_command(
    name: str = typer.Argument(..., help="Secret name (e.g., 100)"),
    output: Path = typer.Option(Path(".env"), "--output", "-o", help="Output file"),
    stdout: bool = typer.Option(False, "--stdout", help="Output to stdout"),
    lowercase: bool = typer.Option(False, "--lowercase", "-l", help="Use lowercase keys"),
    no_transform: bool = typer.Option(False, "--no-transform", "-n", help="Keep original key names"),
):
    """Generate .env file from Vault."""
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    try:
        data = client.kv_get(settings.kv_mount, secret_path)
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] Secret not found: {name}")
        else:
            console.print(f"[red]✗[/red] Failed to retrieve: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] Secret is empty: {name}")
        raise typer.Exit(1)

    if no_transform:
        transformed_data = data
    else:
        transformed_data = {}
        for key, value in data.items():
            if key.startswith("_"):
                continue  # Skip placeholders
            new_key = key.replace("-", "_").replace(".", "_").replace(" ", "_")
            new_key = new_key.lower() if lowercase else new_key.upper()
            transformed_data[new_key] = value

    if stdout:
        for key, value in sorted(transformed_data.items()):
            console.print(f"{key}={value}")
    else:
        write_env_file(str(output), transformed_data, header=f"Generated from Vault: {name}")
        console.print(f"[green]✓[/green] {output} ({len(transformed_data)} variables)")


@app.command("status")
def status_command():
    """Show connection and auth status."""
    console.print("[bold]vaultctl Status[/bold]\n")
    
    console.print("1. Configuration")
    console.print(f"   Vault: {settings.vault_addr}")
    console.print(f"   KV: {settings.kv_mount}/{settings.kv_path}/")
    
    console.print("\n2. Connection")
    client = VaultClient()
    health = client.health()
    
    if health.get("initialized") and not health.get("sealed"):
        console.print("   [green]✓[/green] Vault server connected")
    else:
        console.print("   [red]✗[/red] Vault server connection failed")
        raise typer.Exit(1)
    
    console.print("\n3. Authentication")
    try:
        client = _get_authenticated_client()
        token_info = client.token_lookup()
        data = token_info.get("data", {})
        
        console.print("   [green]✓[/green] Authenticated")
        ttl = data.get("ttl", 0)
        console.print(f"   TTL: {format_duration(ttl) if ttl else '[green]unlimited[/green]'}")
    except typer.Exit:
        console.print("   [red]✗[/red] Authentication required")
        raise
    
    console.print("\n4. Secrets Access")
    try:
        items = client.kv_list(settings.kv_mount, settings.kv_path)
        console.print(f"   [green]✓[/green] {len(items) if items else 0} secrets accessible")
    except VaultError as e:
        console.print(f"   [yellow]![/yellow] {e.message}")
    
    console.print("\n[green]✓[/green] All checks passed")


@app.command("config")
def config_command():
    """Show current configuration."""
    table = Table(title="Current Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="green")
    table.add_column("Value", style="white")

    configs = [
        ("Vault Address", settings.vault_addr),
        ("KV Mount", settings.kv_mount),
        ("KV Path", settings.kv_path),
        ("Full Path", f"{settings.kv_mount}/data/{settings.kv_path}/<n>"),
        ("Config Directory", str(settings.config_dir)),
    ]

    for name, value in configs:
        table.add_row(name, str(value) if value else "-")

    console.print(table)


if __name__ == "__main__":
    app()
