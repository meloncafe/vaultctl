"""Admin setup commands.
관리자 설정 명령어.

Commands:
    vaultctl admin setup vault        # Create Vault policies and AppRoles
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

# Two-tier AppRole model:
#   - ADMIN: full CRUD, used from the admin workstation (admin put/get/...).
#   - RO:    read-only, distributed to hosts for runtime (compose sync/env/run).
# Splitting them means a leaked runtime Secret ID can only read the subtree,
# never overwrite or delete sibling secrets.
ADMIN_ROLE = "vaultctl"
ADMIN_POLICY = "vaultctl"
RO_ROLE = "vaultctl-ro"
RO_POLICY = "vaultctl-ro"


@app.command("vault")
def setup_vault(
    generate_secret: bool = typer.Option(False, "--generate-secret", "-g", help="Generate a new Secret ID for an existing AppRole"),
    role: str = typer.Option(RO_ROLE, "--role", "-r", help=f"AppRole to mint a Secret ID for in -g mode ({RO_ROLE} = runtime/read-only, {ADMIN_ROLE} = admin)"),
):
    """Setup Vault policies and AppRoles / Vault 정책 및 AppRole 생성.

    Creates two policy/AppRole pairs:
        - vaultctl     full CRUD  (admin workstation)
        - vaultctl-ro  read-only  (runtime hosts)

    Use --generate-secret (-g) to mint a new Secret ID for an existing AppRole;
    choose which one with --role (default: vaultctl-ro).
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

    # Generate Secret ID only mode
    if generate_secret:
        console.print(f"\n[bold]Generating new Secret ID for '{role}'...[/bold]")
        try:
            client._request("GET", f"auth/approle/role/{role}")

            role_id_resp = client._request("GET", f"auth/approle/role/{role}/role-id")
            role_id = role_id_resp.get("data", {}).get("role_id")

            secret_id_resp = client._request("POST", f"auth/approle/role/{role}/secret-id")
            secret_id = secret_id_resp.get("data", {}).get("secret_id")

            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            console.print(f"[yellow]Save these credentials securely! (role: {role})[/yellow]")
            console.print(f"[yellow]{'─' * 60}[/yellow]")
            console.print(f"\n  Role ID:    {role_id}")
            console.print(f"  Secret ID:  {secret_id}  [dim](newly generated)[/dim]")
            console.print(f"\n[yellow]{'─' * 60}[/yellow]")
            return

        except VaultError:
            console.print(f"[red]✗[/red] AppRole '{role}' not found. Run without -g to create it.")
            raise typer.Exit(1)

    # Full setup - get path configuration
    console.print("\n[bold]KV Path Configuration[/bold]")
    console.print("[dim]This determines where secrets are stored and what the policies allow.[/dim]")

    kv_mount = Prompt.ask("KV engine mount", default="kv")
    kv_path = Prompt.ask("Secret base path", default="proxmox/lxc")

    kv_path = kv_path.strip("/")
    if not kv_path:
        console.print("[red]✗[/red] Secret base path must not be empty.")
        raise typer.Exit(1)

    # The policy wildcard is scoped to the FIRST path segment only. With a
    # single-segment base (e.g. "apps") the scope matches the base exactly. With
    # a multi-segment base (e.g. "proxmox/lxc") the policy is WIDER than the base
    # — it grants the whole first segment (kv/data/proxmox/*), not just the base.
    # Keep the base single-segment unless you intend that wider scope.
    policy_path = kv_path.split("/")[0] if "/" in kv_path else kv_path

    console.print(Panel.fit(
        "[bold blue]Vault Setup[/bold blue]\n\n"
        "This will create two policy/AppRole pairs:\n"
        f"• {ADMIN_POLICY}      — full CRUD (admin workstation)\n"
        f"• {RO_POLICY}   — read-only (runtime hosts)\n\n"
        f"Access scope: {kv_mount}/data/{policy_path}/*",
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

    # 2. Create policies (admin = full CRUD, ro = read-only)
    console.print("\n[bold]2. Policies[/bold]")

    admin_policy_hcl = f'''# vaultctl admin policy (full CRUD)
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

    ro_policy_hcl = f'''# vaultctl runtime policy (read-only)
# Read access to {kv_mount}/{policy_path}/*

path "{kv_mount}/data/{policy_path}/*" {{
  capabilities = ["read"]
}}

path "{kv_mount}/metadata/{policy_path}/*" {{
  capabilities = ["list", "read"]
}}

path "auth/token/lookup-self" {{
  capabilities = ["read"]
}}

path "auth/token/renew-self" {{
  capabilities = ["update"]
}}
'''

    for p_name, p_hcl, desc in (
        (ADMIN_POLICY, admin_policy_hcl, "full CRUD"),
        (RO_POLICY, ro_policy_hcl, "read-only"),
    ):
        try:
            client._request("PUT", f"sys/policies/acl/{p_name}", data={"policy": p_hcl})
            console.print(f"   [green]✓[/green] {p_name} ({desc})")
        except VaultError as e:
            console.print(f"   [red]✗[/red] Failed ({p_name}): {e.message}")
            raise typer.Exit(1)

    console.print(f"   [dim]Scope: {kv_mount}/data/{policy_path}/*[/dim]")

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

    # 4. Create AppRoles (admin + read-only). Existing roles are left as-is so
    #    previously distributed credentials keep working.
    console.print("\n[bold]4. AppRoles[/bold]")

    for r_name, p_name in ((ADMIN_ROLE, ADMIN_POLICY), (RO_ROLE, RO_POLICY)):
        exists = False
        try:
            client._request("GET", f"auth/approle/role/{r_name}")
            exists = True
            console.print(f"   [green]✓[/green] Already exists: {r_name}")
        except VaultError:
            pass

        if not exists:
            try:
                client._request("POST", f"auth/approle/role/{r_name}", data={
                    "token_policies": [p_name],
                    "token_ttl": "1h",
                    "token_max_ttl": "24h",
                    "secret_id_ttl": "0",
                    "secret_id_num_uses": 0,
                })
                console.print(f"   [green]✓[/green] Created: {r_name}")
            except VaultError as e:
                console.print(f"   [red]✗[/red] Failed ({r_name}): {e.message}")
                raise typer.Exit(1)

    # 5. Role IDs + a runtime (read-only) Secret ID for hosts.
    console.print("\n[bold]5. Credentials[/bold]")
    try:
        admin_role_id = client._request(
            "GET", f"auth/approle/role/{ADMIN_ROLE}/role-id"
        ).get("data", {}).get("role_id")
        ro_role_id = client._request(
            "GET", f"auth/approle/role/{RO_ROLE}/role-id"
        ).get("data", {}).get("role_id")
        ro_secret_id = client._request(
            "POST", f"auth/approle/role/{RO_ROLE}/secret-id"
        ).get("data", {}).get("secret_id")

        console.print(f"\n[yellow]{'─' * 60}[/yellow]")
        console.print("[yellow]Save these credentials securely![/yellow]")
        console.print(f"[yellow]{'─' * 60}[/yellow]")
        console.print("\n  Runtime (read-only) — for each host:")
        console.print(f"    Role ID:    {ro_role_id}")
        console.print(f"    Secret ID:  {ro_secret_id}  [dim](newly generated)[/dim]")
        console.print("\n  Admin (full CRUD) — workstation only:")
        console.print(f"    Role ID:    {admin_role_id}")
        console.print(f"    [dim]Secret ID: vaultctl admin setup vault -g -r {ADMIN_ROLE}[/dim]")
        console.print(f"\n  KV Mount:   {kv_mount}")
        console.print(f"  KV Path:    {kv_path}")
        console.print(f"\n[yellow]{'─' * 60}[/yellow]")

    except VaultError as e:
        console.print(f"   [red]✗[/red] Failed: {e.message}")
        raise typer.Exit(1)

    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Two AppRoles created:\n"
        f"  • {ADMIN_ROLE}     (admin, full CRUD)  — workstation\n"
        f"  • {RO_ROLE}  (runtime, read-only) — hosts\n\n"
        "On each host (read-only):\n"
        f"  vaultctl init --role {RO_ROLE}\n\n"
        "When prompted, enter:\n"
        f"  KV Mount: {kv_mount}\n"
        f"  KV Path:  {kv_path}\n\n"
        "Generate a new Secret ID later:\n"
        f"  vaultctl admin setup vault -g                  # runtime (default)\n"
        f"  vaultctl admin setup vault -g -r {ADMIN_ROLE}       # admin",
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
