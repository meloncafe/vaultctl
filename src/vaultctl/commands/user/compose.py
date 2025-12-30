"""Docker Compose integration commands for vaultctl."""
import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from ruamel.yaml import YAML

from vaultctl.config import settings
from vaultctl.utils import write_env_file
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(name="compose", help="Docker Compose integration", no_args_is_help=True)
console = Console()

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _get_authenticated_client() -> VaultClient:
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
            result = client.approle_login(settings.approle_role_id, settings.approle_secret_id, settings.approle_mount)
            token = result.get("auth", {}).get("client_token")
            if token:
                return VaultClient(token=token)
        except VaultError:
            pass
    console.print("[red]✗[/red] Authentication required. Run: vaultctl init")
    raise typer.Exit(1)


def _get_secrets(name: str) -> dict:
    client = _get_authenticated_client()
    try:
        return client.kv_get(settings.kv_mount, settings.get_secret_path(name))
    except VaultError:
        return {}


def _detect_docker_compose() -> Tuple[str, List[str]]:
    try:
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
        if result.returncode == 0:
            return ("docker compose", ["docker", "compose"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(["docker-compose", "version"], capture_output=True, timeout=10)
        if result.returncode == 0:
            return ("docker-compose", ["docker-compose"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    console.print("[red]✗[/red] Docker Compose not found.")
    raise typer.Exit(1)


def _find_compose_file(file_path: Optional[Path] = None) -> Path:
    if file_path and file_path.exists():
        return file_path
    for c in [Path("docker-compose.yml"), Path("docker-compose.yaml"), Path("compose.yml"), Path("compose.yaml")]:
        if c.exists():
            return c
    console.print("[red]✗[/red] docker-compose.yml not found.")
    raise typer.Exit(1)


def _run_compose(cmd: List[str], compose_file: Path, docker_cmd: List[str], capture: bool = False):
    full_cmd = docker_cmd + ["-f", str(compose_file)] + cmd
    return subprocess.run(full_cmd, capture_output=capture, text=capture)


def _sync_secrets(name: str, output_path: Path) -> int:
    secrets = _get_secrets(name)
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        raise typer.Exit(1)
    transformed = {k.replace("-", "_").replace(".", "_").upper(): v for k, v in secrets.items()}
    write_env_file(str(output_path), transformed, header=f"Vault secret: {name}")
    try:
        output_path.chmod(0o600)
    except OSError:
        pass
    return len(transformed)


@app.command("init")
def init_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
    services: Optional[str] = typer.Option(None, "--services", "-s"),
    no_backup: bool = typer.Option(False, "--no-backup"),
):
    """Initialize Docker Compose with Vault secrets."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    console.print(f"[green]✓[/green] Docker: {docker_name}, File: {compose_file}")
    
    if not name:
        name = Prompt.ask("Vault secret name")
    secrets = _get_secrets(name)
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        raise typer.Exit(1)
    
    output_file = compose_file.parent / ".env.secrets"
    count = _sync_secrets(name, output_file)
    console.print(f"[green]✓[/green] Created {output_file.name} ({count} variables)")
    
    with open(compose_file) as f:
        compose_data = _yaml.load(f)
    
    target_services = services.split(",") if services else list(compose_data.get("services", {}).keys())
    changes = False
    for svc in target_services:
        service = compose_data.get("services", {}).get(svc, {})
        env_file = service.get("env_file", [])
        if isinstance(env_file, str):
            env_file = [env_file]
        if ".env.secrets" not in env_file:
            env_file.append(".env.secrets")
            service["env_file"] = env_file
            changes = True
    
    if changes:
        if not no_backup:
            shutil.copy(compose_file, compose_file.with_suffix(f".yml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
        with open(compose_file, "w") as f:
            _yaml.dump(compose_data, f)
        console.print(f"[green]✓[/green] Updated {compose_file}")
    
    console.print(Panel.fit(f"[green]Complete![/green]\nUsage: vaultctl compose up {name}", title="OK"))


@app.command("up")
def up_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
    pull: bool = typer.Option(False, "--pull", "-p"),
    build: bool = typer.Option(False, "--build", "-b"),
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d"),
):
    """Sync secrets and start containers."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    if not name:
        name = Prompt.ask("Vault secret name")
    
    count = _sync_secrets(name, compose_file.parent / ".env.secrets")
    console.print(f"[green]✓[/green] Synced {count} secrets")
    
    if pull:
        _run_compose(["pull"], compose_file, docker_cmd)
    cmd = ["up"] + (["-d"] if detach else []) + (["--build"] if build else [])
    result = _run_compose(cmd, compose_file, docker_cmd)
    raise typer.Exit(result.returncode)


@app.command("down")
def down_command(
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
    volumes: bool = typer.Option(False, "--volumes", "-v"),
):
    """Stop containers."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    cmd = ["down"] + (["-v"] if volumes else [])
    result = _run_compose(cmd, compose_file, docker_cmd)
    raise typer.Exit(result.returncode)


@app.command("restart")
def restart_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
    pull: bool = typer.Option(False, "--pull", "-p"),
):
    """Sync secrets and restart containers."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    if not name:
        name = Prompt.ask("Vault secret name")
    
    count = _sync_secrets(name, compose_file.parent / ".env.secrets")
    console.print(f"[green]✓[/green] Synced {count} secrets")
    
    if pull:
        _run_compose(["pull"], compose_file, docker_cmd)
    _run_compose(["down"], compose_file, docker_cmd)
    result = _run_compose(["up", "-d"], compose_file, docker_cmd)
    raise typer.Exit(result.returncode)


@app.command("logs")
def logs_command(
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
    follow: bool = typer.Option(False, "--follow"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
):
    """Show container logs."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    cmd = ["logs"] + (["-f"] if follow else []) + (["--tail", str(tail)] if tail else []) + ([service] if service else [])
    result = _run_compose(cmd, compose_file, docker_cmd)
    raise typer.Exit(result.returncode)


@app.command("status")
def status_command(file: Optional[Path] = typer.Option(None, "--file", "-f")):
    """Show container status."""
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    _run_compose(["ps"], compose_file, docker_cmd)


@app.command("sync")
def sync_command(
    name: str = typer.Argument(..., help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f"),
):
    """Sync secrets without restarting."""
    compose_file = _find_compose_file(file)
    count = _sync_secrets(name, compose_file.parent / ".env.secrets")
    console.print(f"[green]✓[/green] Synced {count} secrets. Restart to apply.")
