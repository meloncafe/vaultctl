"""Admin setup commands.
관리자 설정 명령어.

Commands:
    vaultctl admin setup vault        # Create Vault policy and AppRole
    vaultctl admin setup apt-server   # Build APT repository server
    vaultctl admin setup apt-client   # Configure APT client
"""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from vaultctl.config import settings
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(help="Setup commands / 설정 명령어")
console = Console()


@app.command("vault")
def setup_vault(
    generate_secret: bool = typer.Option(False, "--generate-secret", "-g", help="Generate new Secret ID for existing AppRole"),
):
    """Setup Vault policy and AppRole / Vault 정책 및 AppRole 생성.
    
    Creates:
        - Policy: vaultctl (read/write to kv/<path>/*)
        - AppRole: vaultctl
    
    Use --generate-secret to create a new Secret ID for an existing AppRole.
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
            client._request("GET", f"auth/approle/role/{role_name}")
            
            role_id_resp = client._request("GET", f"auth/approle/role/{role_name}/role-id")
            role_id = role_id_resp.get("data", {}).get("role_id")
            
            secret_id_resp = client._request("POST", f"auth/approle/role/{role_name}/secret-id")
            secret_id = secret_id_resp.get("data", {}).get("secret_id")
            
            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            console.print("[yellow]Save these credentials securely![/yellow]")
            console.print(f"[yellow]{'─' * 60}[/yellow]")
            console.print(f"\n  Role ID:    {role_id}")
            console.print(f"  Secret ID:  {secret_id}  [dim](newly generated)[/dim]")
            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            return
            
        except VaultError:
            console.print(f"[red]✗[/red] AppRole '{role_name}' not found. Run without -g to create it.")
            raise typer.Exit(1)
    
    # Full setup - get path configuration
    console.print("\n[bold]KV Path Configuration[/bold]")
    console.print("[dim]This determines where secrets are stored and what the policy allows.[/dim]")
    
    kv_mount = Prompt.ask("KV engine mount", default="kv")
    kv_path = Prompt.ask("Secret base path", default="proxmox/lxc")
    
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
                "secret_id_ttl": "0",
                "secret_id_num_uses": 0,
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


@app.command("apt-server")
def setup_apt_server():
    """Setup APT repository server / APT 저장소 서버 구축."""
    # Import from the existing setup module
    from vaultctl.commands.admin.apt_setup import apt_server_setup
    apt_server_setup(reconfigure=False)


@app.command("apt-client")
def setup_apt_client(
    url: str = typer.Argument(..., help="APT repository URL"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Auth username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Auth password"),
):
    """Setup APT client / APT 클라이언트 설정."""
    from vaultctl.commands.admin.apt_setup import apt_client_setup
    apt_client_setup(url=url, user=user, password=password, codename="stable", remove=False)
