"""Docker Compose integration commands for vaultctl.
Docker Compose 통합 명령어.

Commands:
- vaultctl compose init: Initialize .env.secrets and update docker-compose.yml
- vaultctl compose up: Sync secrets and run docker compose up
- vaultctl compose down: Run docker compose down
- vaultctl compose restart: Sync secrets and restart
- vaultctl compose pull: Pull images
- vaultctl compose logs: Show logs
- vaultctl compose status: Show container and secret status
- vaultctl compose prune: Clean up old images
"""
import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from ruamel.yaml import YAML

from vaultctl.config import settings
from vaultctl.templates import render_template
from vaultctl.utils import write_env_file
from vaultctl.vault_client import VaultClient, VaultError

app = typer.Typer(
    name="compose",
    help="Docker Compose integration / Docker Compose 통합",
    no_args_is_help=True,
)
console = Console()

# YAML parser (preserves comments)
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client / 인증된 클라이언트 반환."""
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


def _get_secrets(name: str) -> dict:
    """Get secrets from Vault / Vault에서 시크릿 조회."""
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    try:
        return client.kv_get(settings.kv_mount, secret_path)
    except VaultError:
        return {}


def _detect_docker_compose() -> Tuple[str, List[str]]:
    """Detect docker compose command / docker compose 명령어 감지.
    
    Returns:
        Tuple of (display_name, command_list)
    """
    # Try docker compose (v2) first
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return ("docker compose", ["docker", "compose"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Try docker-compose (v1)
    try:
        result = subprocess.run(
            ["docker-compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return ("docker-compose", ["docker-compose"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    console.print("[red]✗[/red] Docker Compose not found.")
    console.print("  Install docker compose or docker-compose.")
    raise typer.Exit(1)


def _find_compose_file(file_path: Optional[Path] = None) -> Path:
    """Find docker-compose.yml / docker-compose.yml 찾기."""
    if file_path:
        if file_path.exists():
            return file_path
        console.print(f"[red]✗[/red] File not found: {file_path}")
        raise typer.Exit(1)
    
    # Search order
    candidates = [
        Path("docker-compose.yml"),
        Path("docker-compose.yaml"),
        Path("compose.yml"),
        Path("compose.yaml"),
    ]
    
    for candidate in candidates:
        if candidate.exists():
            return candidate
    
    console.print("[red]✗[/red] docker-compose.yml not found in current directory.")
    console.print("  Use -f to specify the file path.")
    raise typer.Exit(1)


def _parse_compose_file(file_path: Path) -> dict:
    """Parse docker-compose.yml / docker-compose.yml 파싱."""
    with open(file_path) as f:
        return _yaml.load(f)


def _save_compose_file(file_path: Path, data: dict) -> None:
    """Save docker-compose.yml / docker-compose.yml 저장."""
    with open(file_path, "w") as f:
        _yaml.dump(data, f)


def _get_services(compose_data: dict) -> List[str]:
    """Get service names from compose data / 서비스 이름 목록."""
    services = compose_data.get("services", {})
    return list(services.keys())


def _run_compose(
    cmd: List[str],
    compose_file: Path,
    docker_cmd: List[str],
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run docker compose command / docker compose 명령 실행."""
    full_cmd = docker_cmd + ["-f", str(compose_file)] + cmd
    
    if capture:
        return subprocess.run(full_cmd, capture_output=True, text=True)
    else:
        return subprocess.run(full_cmd)


def _sync_secrets(name: str, output_path: Path) -> int:
    """Sync secrets to .env file / 시크릿을 .env 파일로 동기화.
    
    Returns:
        Number of variables synced
    """
    secrets = _get_secrets(name)
    
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        raise typer.Exit(1)
    
    # Transform keys to UPPER_CASE
    transformed = {}
    for key, value in secrets.items():
        new_key = key.replace("-", "_").replace(".", "_").replace(" ", "_").upper()
        transformed[new_key] = value
    
    write_env_file(str(output_path), transformed, header=f"Vault secret: {name}")
    
    # Set file permissions (Unix only)
    try:
        output_path.chmod(0o600)
    except (OSError, AttributeError):
        pass
    
    return len(transformed)


def _get_secrets_hash(name: str) -> str:
    """Get hash of secrets for change detection / 변경 감지용 해시."""
    secrets = _get_secrets(name)
    if not secrets:
        return ""
    content = str(sorted(secrets.items()))
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def _render_ctl_script(
    compose_file: Path,
    secret_name: str,
    secrets_file: str,
    docker_cmd: List[str],
) -> str:
    """Render ctl.sh script from template / 템플릿에서 ctl.sh 스크립트 렌더링."""
    return render_template("compose/ctl.sh.j2", {
        "compose_file": compose_file.name,
        "secret_name": secret_name,
        "secrets_file": secrets_file,
        "docker_cmd": " ".join(docker_cmd),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════════════════


@app.command("init")
def init_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name / Vault 시크릿 이름"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path / Compose 파일 경로"),
    services: Optional[str] = typer.Option(None, "--services", "-s", help="Services to update (comma-separated) / 업데이트할 서비스"),
    script: bool = typer.Option(False, "--script", help="Generate ctl.sh script / ctl.sh 스크립트 생성"),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip backup / 백업 생략"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations / 확인 생략"),
):
    """Initialize Docker Compose with Vault secrets.
    Vault 시크릿으로 Docker Compose 초기화.
    
    Creates .env.secrets file and updates docker-compose.yml to use it.
    .env.secrets 파일을 생성하고 docker-compose.yml에 env_file을 추가합니다.
    
    \b
    Examples:
        vaultctl compose init              # Interactive mode
        vaultctl compose init 100          # With secret name
        vaultctl compose init 100 -s web   # Specific service
        vaultctl compose init 100 --script # Generate ctl.sh
    """
    console.print(Panel.fit(
        "[bold blue]Docker Compose + Vault Setup[/bold blue]\n\n"
        "This will:\n"
        "1. Generate .env.secrets from Vault\n"
        "2. Update docker-compose.yml to use it\n"
        "3. Optionally generate management script",
        title="🐳 Compose Init",
    ))
    console.print()
    
    # 1. Detect docker compose
    docker_name, docker_cmd = _detect_docker_compose()
    console.print(f"[green]✓[/green] Docker Compose: {docker_name}")
    
    # 2. Find compose file
    compose_file = _find_compose_file(file)
    console.print(f"[green]✓[/green] Compose file: {compose_file}")
    
    # 3. Get secret name (interactive if not provided)
    if not name:
        # List available secrets
        client = _get_authenticated_client()
        try:
            available = client.kv_list(settings.kv_mount, settings.kv_path)
            if available:
                console.print("\n[bold]Available secrets:[/bold]")
                for i, s in enumerate(available[:10], 1):
                    console.print(f"  {i}. {s.rstrip('/')}")
                if len(available) > 10:
                    console.print(f"  ... and {len(available) - 10} more")
                console.print()
        except VaultError:
            pass
        
        name = Prompt.ask("Vault secret name (e.g., 100, n8n)")
        if not name:
            console.print("[red]✗[/red] Secret name is required.")
            raise typer.Exit(1)
    
    # 4. Verify secret exists
    secrets = _get_secrets(name)
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        console.print(f"  Path: {settings.kv_mount}/{settings.get_secret_path(name)}")
        raise typer.Exit(1)
    
    console.print(f"[green]✓[/green] Secret found: {name} ({len(secrets)} variables)")
    
    # 5. Determine output file
    compose_dir = compose_file.parent
    env_file = compose_dir / ".env"
    secrets_file = compose_dir / ".env.secrets"
    
    if env_file.exists():
        # .env exists, use .env.secrets
        output_file = secrets_file
        console.print(f"[dim].env exists, using .env.secrets for Vault secrets[/dim]")
    else:
        # No .env, ask user
        if yes or Confirm.ask("No .env file found. Create .env.secrets?", default=True):
            output_file = secrets_file
        else:
            output_file = env_file
    
    # 6. Generate secrets file
    count = _sync_secrets(name, output_file)
    console.print(f"[green]✓[/green] Created {output_file.name} ({count} variables)")
    
    # 7. Parse compose file
    compose_data = _parse_compose_file(compose_file)
    available_services = _get_services(compose_data)
    
    if not available_services:
        console.print("[yellow]![/yellow] No services found in compose file.")
        raise typer.Exit(1)
    
    console.print(f"\n[bold]Services found:[/bold] {', '.join(available_services)}")
    
    # 8. Select services to update
    if services:
        target_services = [s.strip() for s in services.split(",")]
        # Validate
        invalid = [s for s in target_services if s not in available_services]
        if invalid:
            console.print(f"[red]✗[/red] Unknown services: {', '.join(invalid)}")
            raise typer.Exit(1)
    else:
        # Interactive selection
        if len(available_services) == 1:
            target_services = available_services
        else:
            console.print("\nSelect services to add env_file:")
            console.print("  [dim]Enter 'all' for all services, or comma-separated names[/dim]")
            
            selection = Prompt.ask(
                "Services",
                default="all",
            )
            
            if selection.lower() == "all":
                target_services = available_services
            else:
                target_services = [s.strip() for s in selection.split(",")]
    
    # 9. Check and update compose file
    changes_needed = []
    env_files_to_add = []
    
    # Determine what env_file entries to add
    if env_file.exists() and output_file == secrets_file:
        env_files_to_add = [".env", ".env.secrets"]
    else:
        env_files_to_add = [output_file.name]
    
    for service_name in target_services:
        service = compose_data.get("services", {}).get(service_name, {})
        current_env_file = service.get("env_file", [])
        
        # Normalize to list
        if isinstance(current_env_file, str):
            current_env_file = [current_env_file]
        
        # Check if already configured
        missing = [f for f in env_files_to_add if f not in current_env_file]
        
        if missing:
            changes_needed.append((service_name, missing))
    
    if not changes_needed:
        console.print("\n[green]✓[/green] All selected services already have env_file configured.")
    else:
        # Show changes
        console.print("\n[bold]Changes to docker-compose.yml:[/bold]")
        for service_name, missing in changes_needed:
            console.print(f"  {service_name}: add env_file: {missing}")
        
        # Confirm
        if not yes and not Confirm.ask("\nApply changes?", default=True):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
        
        # Backup
        if not no_backup:
            backup_file = compose_file.with_suffix(f".yml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy(compose_file, backup_file)
            console.print(f"[dim]Backup: {backup_file}[/dim]")
        
        # Apply changes
        for service_name, missing in changes_needed:
            service = compose_data["services"][service_name]
            current = service.get("env_file", [])
            
            if isinstance(current, str):
                current = [current]
            
            # Add missing entries
            for entry in missing:
                if entry not in current:
                    current.append(entry)
            
            service["env_file"] = current
        
        # Save
        _save_compose_file(compose_file, compose_data)
        console.print(f"[green]✓[/green] Updated {compose_file}")
    
    # 10. Generate script (optional)
    if script:
        script_file = compose_dir / "ctl.sh"
        script_content = _render_ctl_script(
            compose_file=compose_file,
            secret_name=name,
            secrets_file=output_file.name,
            docker_cmd=docker_cmd,
        )
        script_file.write_text(script_content)
        
        # Make executable (Unix only)
        try:
            script_file.chmod(0o755)
        except (OSError, AttributeError):
            pass
        
        console.print(f"[green]✓[/green] Generated {script_file}")
    
    # 11. Add to .gitignore
    gitignore = compose_dir / ".gitignore"
    entries_to_add = [".env.secrets", ".env", "*.bak.*"]
    
    if gitignore.exists():
        content = gitignore.read_text()
        missing_entries = [e for e in entries_to_add if e not in content]
        
        if missing_entries:
            if yes or Confirm.ask(f"\nAdd {missing_entries} to .gitignore?", default=True):
                with gitignore.open("a") as f:
                    f.write("\n# Vault secrets (vaultctl)\n")
                    for entry in missing_entries:
                        f.write(f"{entry}\n")
                console.print(f"[green]✓[/green] Updated .gitignore")
    else:
        if yes or Confirm.ask("\nCreate .gitignore with secret files?", default=True):
            gitignore.write_text("# Vault secrets (vaultctl)\n" + "\n".join(entries_to_add) + "\n")
            console.print(f"[green]✓[/green] Created .gitignore")
    
    # 12. Done
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        f"Secret: {name}\n"
        f"Env file: {output_file.name}\n\n"
        "Usage:\n"
        f"  vaultctl compose up {name}      # Start with secrets\n"
        f"  vaultctl compose restart {name} # Restart with fresh secrets\n"
        f"  vaultctl compose status         # Check status",
        title="✓ Complete",
    ))


@app.command("up")
def up_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name / Vault 시크릿 이름"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Secrets output file"),
    pull: bool = typer.Option(False, "--pull", "-p", help="Pull images first"),
    build: bool = typer.Option(False, "--build", "-b", help="Build images"),
    prune: bool = typer.Option(False, "--prune", help="Prune old images after"),
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Run in background"),
):
    """Sync secrets and start containers.
    시크릿 동기화 후 컨테이너 시작.
    
    \b
    Examples:
        vaultctl compose up 100
        vaultctl compose up 100 --pull
        vaultctl compose up 100 --build --prune
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    
    # Interactive mode if no name
    if not name:
        name = Prompt.ask("Vault secret name")
        if not name:
            console.print("[red]✗[/red] Secret name is required.")
            raise typer.Exit(1)
    
    # Determine output file
    compose_dir = compose_file.parent
    if output:
        output_file = output
    elif (compose_dir / ".env.secrets").exists():
        output_file = compose_dir / ".env.secrets"
    elif (compose_dir / ".env").exists():
        output_file = compose_dir / ".env.secrets"  # Don't overwrite .env
    else:
        output_file = compose_dir / ".env.secrets"
    
    # Sync secrets
    count = _sync_secrets(name, output_file)
    console.print(f"[green]✓[/green] Synced {count} secrets to {output_file.name}")
    
    # Pull if requested
    if pull:
        console.print("[blue]▶[/blue] Pulling images...")
        _run_compose(["pull"], compose_file, docker_cmd)
    
    # Build command
    cmd = ["up"]
    if detach:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    
    # Start
    console.print(f"[blue]▶[/blue] Starting containers...")
    result = _run_compose(cmd, compose_file, docker_cmd)
    
    # Prune if requested
    if prune and result.returncode == 0:
        console.print("[blue]▶[/blue] Pruning old images...")
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True)
        console.print("[green]✓[/green] Cleaned up old images")
    
    if result.returncode == 0:
        console.print("[green]✓[/green] Containers started")
    
    raise typer.Exit(result.returncode)


@app.command("down")
def down_command(
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Remove volumes"),
    remove_orphans: bool = typer.Option(False, "--remove-orphans", help="Remove orphan containers"),
):
    """Stop containers.
    컨테이너 중지.
    
    \b
    Examples:
        vaultctl compose down
        vaultctl compose down -v  # Remove volumes too
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    
    cmd = ["down"]
    if volumes:
        cmd.append("-v")
    if remove_orphans:
        cmd.append("--remove-orphans")
    
    console.print(f"[blue]▶[/blue] Stopping containers...")
    result = _run_compose(cmd, compose_file, docker_cmd)
    
    if result.returncode == 0:
        console.print("[green]✓[/green] Containers stopped")
    
    raise typer.Exit(result.returncode)


@app.command("restart")
def restart_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Secrets output file"),
    pull: bool = typer.Option(False, "--pull", "-p", help="Pull images first"),
):
    """Sync secrets and restart containers.
    시크릿 동기화 후 컨테이너 재시작.
    
    \b
    Examples:
        vaultctl compose restart 100
        vaultctl compose restart 100 --pull
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    
    if not name:
        name = Prompt.ask("Vault secret name")
        if not name:
            console.print("[red]✗[/red] Secret name is required.")
            raise typer.Exit(1)
    
    # Determine output file
    compose_dir = compose_file.parent
    if output:
        output_file = output
    elif (compose_dir / ".env.secrets").exists():
        output_file = compose_dir / ".env.secrets"
    else:
        output_file = compose_dir / ".env.secrets"
    
    # Sync secrets
    count = _sync_secrets(name, output_file)
    console.print(f"[green]✓[/green] Synced {count} secrets")
    
    # Pull if requested
    if pull:
        console.print("[blue]▶[/blue] Pulling images...")
        _run_compose(["pull"], compose_file, docker_cmd)
    
    # Restart (down + up for env changes to take effect)
    console.print("[blue]▶[/blue] Restarting containers...")
    _run_compose(["down"], compose_file, docker_cmd)
    result = _run_compose(["up", "-d"], compose_file, docker_cmd)
    
    if result.returncode == 0:
        console.print("[green]✓[/green] Containers restarted")
    
    raise typer.Exit(result.returncode)


@app.command("pull")
def pull_command(
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
):
    """Pull container images.
    컨테이너 이미지 풀.
    
    \b
    Examples:
        vaultctl compose pull
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    
    console.print("[blue]▶[/blue] Pulling images...")
    result = _run_compose(["pull"], compose_file, docker_cmd)
    
    if result.returncode == 0:
        console.print("[green]✓[/green] Images pulled")
    
    raise typer.Exit(result.returncode)


@app.command("logs")
def logs_command(
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
    follow: bool = typer.Option(False, "--follow", help="Follow log output"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Number of lines to show"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Specific service"),
):
    """Show container logs.
    컨테이너 로그 출력.
    
    \b
    Examples:
        vaultctl compose logs
        vaultctl compose logs --follow
        vaultctl compose logs -n 100 -s web
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    
    cmd = ["logs"]
    if follow:
        cmd.append("-f")
    if tail:
        cmd.extend(["--tail", str(tail)])
    if service:
        cmd.append(service)
    
    result = _run_compose(cmd, compose_file, docker_cmd)
    raise typer.Exit(result.returncode)


@app.command("status")
def status_command(
    name: Optional[str] = typer.Argument(None, help="Vault secret name (for sync check)"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
):
    """Show container and secret status.
    컨테이너 및 시크릿 상태 확인.
    
    \b
    Examples:
        vaultctl compose status
        vaultctl compose status 100  # Check sync status
    """
    docker_name, docker_cmd = _detect_docker_compose()
    compose_file = _find_compose_file(file)
    compose_dir = compose_file.parent
    
    console.print("[bold]Docker Compose Status[/bold]\n")
    
    # Container status
    console.print("1. Containers")
    result = _run_compose(["ps"], compose_file, docker_cmd, capture=True)
    if result.returncode == 0:
        console.print(result.stdout)
    else:
        console.print("[yellow]   No containers running[/yellow]")
    
    # Secrets file status
    console.print("\n2. Secret Files")
    
    secrets_files = [".env", ".env.secrets"]
    for sf in secrets_files:
        sf_path = compose_dir / sf
        if sf_path.exists():
            stat = sf_path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"   [green]✓[/green] {sf} (modified: {mtime})")
        else:
            console.print(f"   [dim]✗ {sf} (not found)[/dim]")
    
    # Vault sync status
    if name:
        console.print(f"\n3. Vault Sync ({name})")
        
        # Get current Vault hash
        vault_hash = _get_secrets_hash(name)
        
        if vault_hash:
            console.print(f"   Vault hash: {vault_hash}")
            
            # Check local file
            secrets_file = compose_dir / ".env.secrets"
            if secrets_file.exists():
                local_content = secrets_file.read_text()
                # Simple check - see if file header matches
                if f"Vault secret: {name}" in local_content:
                    console.print(f"   [green]✓[/green] Synced with Vault")
                else:
                    console.print(f"   [yellow]![/yellow] May be out of sync")
            else:
                console.print(f"   [yellow]![/yellow] .env.secrets not found")
        else:
            console.print(f"   [red]✗[/red] Cannot read Vault secret")


@app.command("prune")
def prune_command(
    all_images: bool = typer.Option(False, "--all", "-a", help="Remove all unused images"),
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Remove unused volumes"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
):
    """Clean up unused Docker resources.
    사용하지 않는 Docker 리소스 정리.
    
    \b
    Examples:
        vaultctl compose prune
        vaultctl compose prune --all
        vaultctl compose prune --volumes
    """
    if not force:
        if not Confirm.ask("Remove unused Docker resources?", default=False):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
    
    # Prune images
    console.print("[blue]▶[/blue] Removing unused images...")
    cmd = ["docker", "image", "prune", "-f"]
    if all_images:
        cmd.append("-a")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        # Parse output
        if "Total reclaimed space" in result.stdout:
            for line in result.stdout.split("\n"):
                if "Total reclaimed space" in line:
                    console.print(f"   {line}")
        else:
            console.print("   No images to remove")
    
    # Prune volumes
    if volumes:
        console.print("[blue]▶[/blue] Removing unused volumes...")
        result = subprocess.run(
            ["docker", "volume", "prune", "-f"],
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 0:
            if "Total reclaimed space" in result.stdout:
                for line in result.stdout.split("\n"):
                    if "Total reclaimed space" in line:
                        console.print(f"   {line}")
            else:
                console.print("   No volumes to remove")
    
    console.print("[green]✓[/green] Cleanup complete")


@app.command("sync")
def sync_command(
    name: str = typer.Argument(..., help="Vault secret name"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Compose file path"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
):
    """Sync secrets without restarting containers.
    컨테이너 재시작 없이 시크릿만 동기화.
    
    \b
    Examples:
        vaultctl compose sync 100
        vaultctl compose sync 100 -o .env.secrets
    """
    compose_file = _find_compose_file(file)
    compose_dir = compose_file.parent
    
    if output:
        output_file = output
    elif (compose_dir / ".env.secrets").exists():
        output_file = compose_dir / ".env.secrets"
    else:
        output_file = compose_dir / ".env.secrets"
    
    count = _sync_secrets(name, output_file)
    console.print(f"[green]✓[/green] Synced {count} secrets to {output_file}")
    console.print("[dim]Note: Restart containers to apply changes[/dim]")
