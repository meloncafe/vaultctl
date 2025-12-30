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
    
    vaultctl admin repo add           # Add package to APT repo
    vaultctl admin repo sync          # Sync from GitHub
"""

import typer

from vaultctl.commands.admin import secrets, token, credentials, setup, repo

app = typer.Typer(
    name="admin",
    help="Administrator commands / 관리자 명령어",
    no_args_is_help=True,
)

# Sub-apps
app.add_typer(setup.app, name="setup")
app.add_typer(token.app, name="token")
app.add_typer(repo.app, name="repo")

# Secret management commands
app.command("list")(secrets.list_secrets)
app.command("get")(secrets.get_secret)
app.command("put")(secrets.put_secret)
app.command("delete")(secrets.delete_secret)
app.command("import")(secrets.import_secrets)
app.command("export")(secrets.export_secrets)

# Credentials command
app.command("credentials")(credentials.get_credentials)
