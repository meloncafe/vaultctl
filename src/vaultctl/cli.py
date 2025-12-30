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
import socket
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from vaultctl import __version__
import vaultctl.commands.admin as admin
import vaultctl.commands.compose as compose
import vaultctl.commands.extended as extended
import vaultctl.commands.repo as repo
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

# Sub-commands
app.add_typer(admin.app, name="admin", help="Administrator commands / 관리자 명령어")
app.add_typer(compose.app, name="compose", help="Docker Compose integration / Docker Compose 통합")
admin.app.add_typer(repo.app, name="repo", help="APT package management / APT 패키지 관리")

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
        vaultctl env 100           # Generate .env file
        docker compose up -d       # Run
    
    \b
    Docker Compose:
        vaultctl compose init 100            # Setup compose + secrets
        vaultctl compose up 100              # Sync secrets & start
        vaultctl compose restart 100         # Sync & restart
        vaultctl compose status              # Check status
    
    \b
    Advanced:
        vaultctl run 100 -- node app.js       # Run with injected env vars
        vaultctl watch 100 -- docker compose up  # Auto-restart
        eval "$(vaultctl sh 100)"             # Load shell env vars
    
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
    role_name: str = typer.Option("vaultctl", "--role", "-r", help="AppRole name / AppRole 이름"),
):
    """Initialize vaultctl (one-time setup) / 초기 설정 (한 번만).
    
    Connects to Vault with admin token and automatically generates
    AppRole credentials for this machine.
    Admin 토큰으로 Vault에 연결하고 이 머신용 AppRole 자격 증명을 자동 생성합니다.
    
    \b
    Prerequisites:
        - AppRole must be created first on admin workstation:
          vaultctl admin setup vault
    
    \b
    Examples:
        vaultctl init                  # Use default AppRole 'vaultctl'
        vaultctl init -r myapp         # Use custom AppRole name
    """
    console.print(Panel.fit(
        "[bold blue]vaultctl Initial Setup[/bold blue]\n\n"
        "Connect to Vault and generate AppRole credentials.\n"
        "Requires admin token to generate Secret ID.",
        title="🔐 Setup",
    ))
    console.print()
    
    # 1. Vault address
    current_addr = settings.vault_addr
    has_valid_addr = current_addr and current_addr != "https://vault.example.com"
    
    vault_addr = Prompt.ask(
        "Vault server address",
        default=current_addr if has_valid_addr else None,
    )
    
    if not vault_addr:
        console.print("[red]✗[/red] Vault address is required.")
        raise typer.Exit(1)
    
    # Test connection
    console.print(f"\n[dim]Connecting to {vault_addr}...[/dim]")
    client = VaultClient(addr=vault_addr)
    health = client.health()
    
    if not health.get("initialized"):
        console.print("[red]✗[/red] Cannot connect to Vault server.")
        raise typer.Exit(1)
    
    if health.get("sealed"):
        console.print("[red]✗[/red] Vault server is sealed.")
        raise typer.Exit(1)
    
    console.print("[green]✓[/green] Connection successful")
    
    # 2. Admin token for Secret ID generation
    console.print("\n[bold]Admin Authentication[/bold]")
    console.print("[dim]Admin token is required to generate Secret ID for this machine.[/dim]")
    
    admin_token = Prompt.ask("Admin/Root token", password=True)
    
    if not admin_token:
        console.print("[red]✗[/red] Admin token is required.")
        raise typer.Exit(1)
    
    # Test admin token
    admin_client = VaultClient(addr=vault_addr, token=admin_token)
    try:
        admin_client.token_lookup()
        console.print("[green]✓[/green] Admin authentication successful")
    except VaultError as e:
        console.print(f"[red]✗[/red] Admin authentication failed: {e.message}")
        raise typer.Exit(1)
    
    # 3. KV path settings
    console.print("\n[bold]KV Secret Path[/bold]")
    
    current_kv_mount = settings.kv_mount if settings.kv_mount != "kv" else None
    current_kv_path = settings.kv_path if settings.kv_path != "proxmox/lxc" else None
    
    kv_mount = Prompt.ask(
        "KV engine mount",
        default=current_kv_mount or "kv",
    )
    kv_path = Prompt.ask(
        "Secret path",
        default=current_kv_path or "proxmox/lxc",
    )
    
    # Remove leading/trailing slashes
    kv_path = kv_path.strip("/")
    
    # 4. AppRole name (allow change)
    console.print(f"\n[bold]AppRole[/bold]")
    role_name = Prompt.ask("AppRole name", default=role_name)
    
    # 5. Check AppRole exists
    console.print(f"\n[dim]Checking AppRole '{role_name}'...[/dim]")
    
    try:
        role_info = admin_client.approle_read_role(role_name)
        policies = role_info.get("token_policies", [])
        console.print(f"[green]✓[/green] AppRole found: {role_name}")
        console.print(f"   [dim]Policies: {', '.join(policies)}[/dim]")
    except VaultError as e:
        console.print(f"[red]✗[/red] AppRole '{role_name}' not found.")
        console.print("\n  First run on admin workstation:")
        console.print("    vaultctl admin setup vault")
        raise typer.Exit(1)
    
    # 6. Get Role ID
    try:
        role_id = admin_client.approle_get_role_id(role_name)
        console.print(f"[green]✓[/green] Role ID retrieved")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to get Role ID: {e.message}")
        raise typer.Exit(1)
    
    # 7. Generate new Secret ID for this machine
    hostname = socket.gethostname()
    console.print(f"\n[dim]Generating Secret ID for {hostname}...[/dim]")
    
    try:
        secret_data = admin_client.approle_generate_secret_id(
            role_name=role_name,
            metadata={
                "generated_by": "vaultctl init",
                "hostname": hostname,
            },
        )
        secret_id = secret_data.get("secret_id", "")
        console.print(f"[green]✓[/green] Secret ID generated")
    except VaultError as e:
        console.print(f"[red]✗[/red] Failed to generate Secret ID: {e.message}")
        raise typer.Exit(1)
    
    # 8. Test AppRole login with generated credentials
    console.print("\n[dim]Testing AppRole authentication...[/dim]")
    try:
        result = client.approle_login(role_id, secret_id, settings.approle_mount)
        token = result.get("auth", {}).get("client_token")
        
        if not token:
            console.print("[red]✗[/red] Authentication failed: no token received.")
            raise typer.Exit(1)
        
        console.print("[green]✓[/green] AppRole authentication successful")
        
        auth_data = result.get("auth", {})
        console.print(f"   Policies: {', '.join(auth_data.get('policies', []))}")
        ttl = auth_data.get("lease_duration", 0)
        console.print(f"   TTL: {format_duration(ttl)}")
        
    except VaultError as e:
        console.print(f"[red]✗[/red] AppRole authentication failed: {e.message}")
        raise typer.Exit(1)
    
    # 9. Test secret access
    console.print("\n[dim]Testing secret access...[/dim]")
    test_client = VaultClient(addr=vault_addr, token=token)
    try:
        items = test_client.kv_list(kv_mount, kv_path)
        console.print(f"[green]✓[/green] Access to {kv_mount}/{kv_path}/ ({len(items) if items else 0} secrets)")
    except VaultError as e:
        console.print(f"[yellow]![/yellow] Warning: Cannot list {kv_mount}/{kv_path}/")
        console.print(f"   {e.message}")
        console.print("   This may be normal if no secrets exist yet.")
    
    # 10. Save configuration
    console.print("\n[dim]Saving configuration...[/dim]")
    
    try:
        settings.ensure_dirs()
        
        # Save config
        config_file = settings.config_dir / "config"
        config_file.write_text(f"""# vaultctl configuration
# Generated on {hostname}
VAULT_ADDR={vault_addr}
VAULT_KV_MOUNT={kv_mount}
VAULT_KV_PATH={kv_path}
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
    
    # 11. Done
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        f"Vault: {vault_addr}\n"
        f"KV Path: {kv_mount}/{kv_path}/\n\n"
        "You can now use:\n"
        "  vaultctl env <n>           # Generate .env file\n"
        "  vaultctl status            # Check status\n"
        "  vaultctl compose init <n>  # Setup Docker Compose",
        title="✓ Complete",
    ))


@app.command("env")
def env_command(
    name: str = typer.Argument(..., help="Secret name (e.g., 100) / 시크릿 이름"),
    output: Path = typer.Option(Path(".env"), "--output", "-o", help="Output file / 출력 파일"),
    stdout: bool = typer.Option(False, "--stdout", help="Output to stdout / stdout으로 출력"),
    lowercase: bool = typer.Option(False, "--lowercase", "-l", help="Use lowercase keys / 소문자 키 사용"),
    no_transform: bool = typer.Option(False, "--no-transform", "-n", help="Keep original key names / 원본 키 이름 유지"),
):
    """Generate .env file from Vault / Vault에서 .env 파일 생성.
    
    By default, keys are transformed to UPPER_CASE (hyphens become underscores).
    기본적으로 키는 대문자로 변환되고 하이픈은 언더바로 변경됩니다.
    
    \b
    Examples:
        vaultctl env 100                  # Generate .env (UPPER_CASE keys)
        vaultctl env 100 -l               # Generate .env (lower_case keys)
        vaultctl env 100 -n               # Keep original key names
        vaultctl env 100 -o prod.env      # Custom filename
        vaultctl env 100 --stdout         # Output to stdout
        
        # Use with docker compose
        vaultctl env 100 && docker compose up -d
    """
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    
    try:
        data = client.kv_get(settings.kv_mount, secret_path)
    except VaultError as e:
        if e.status_code == 404:
            console.print(f"[red]✗[/red] Secret not found: {name}")
            console.print(f"  Path: {settings.kv_mount}/{secret_path}")
            console.print("\n  Ask your administrator to create the secret:")
            console.print(f"    vaultctl admin put {name} KEY=value ...")
        else:
            console.print(f"[red]✗[/red] Failed to retrieve: {e.message}")
        raise typer.Exit(1)

    if not data:
        console.print(f"[yellow]![/yellow] Secret is empty: {name}")
        raise typer.Exit(1)

    # Transform keys
    if no_transform:
        transformed_data = data
    else:
        transformed_data = {}
        for key, value in data.items():
            # Replace hyphens, dots, spaces with underscores
            new_key = key.replace("-", "_").replace(".", "_").replace(" ", "_")
            # Apply case transformation
            if lowercase:
                new_key = new_key.lower()
            else:
                new_key = new_key.upper()
            transformed_data[new_key] = value

    if stdout:
        for key, value in sorted(transformed_data.items()):
            console.print(f"{key}={value}")
    else:
        write_env_file(str(output), transformed_data, header=f"Generated from Vault: {name}")
        console.print(f"[green]✓[/green] {output} ({len(transformed_data)} variables)")


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
    console.print(f"   KV Mount: {settings.kv_mount}")
    console.print(f"   KV Path: {settings.kv_path}/")
    
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
        items = client.kv_list(settings.kv_mount, settings.kv_path)
        console.print(f"   [green]✓[/green] Access to {settings.kv_mount}/{settings.kv_path}/ ({len(items) if items else 0} secrets)")
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
        ("KV Path", settings.kv_path),
        ("Full Path", f"{settings.kv_mount}/data/{settings.kv_path}/<n>"),
        ("AppRole Role ID", settings.approle_role_id[:8] + "..." if settings.approle_role_id else "-"),
        ("Config Directory", str(settings.config_dir)),
        ("Cache Directory", str(settings.cache_dir)),
    ]

    for name, value in configs:
        table.add_row(name, str(value) if value else "-")

    console.print(table)


if __name__ == "__main__":
    app()
