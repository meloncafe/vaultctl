"""APT Repository management commands.
APT 저장소 관리 명령어.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="APT repository management / APT 저장소 관리")
console = Console()

# Constants / 상수
APT_BASE = Path("/var/www/apt")
APT_REPO = APT_BASE / "repo"
APT_GPG_HOME = APT_BASE / ".gnupg"
APT_CONFIG_FILE = APT_BASE / ".config"


def _check_repo_exists() -> None:
    """Check if APT repository is installed / APT 저장소 설치 여부 확인."""
    if not APT_REPO.exists():
        console.print("[red]✗[/red] APT repository not installed.")
        console.print("  Run: sudo vaultctl setup apt-server")
        raise typer.Exit(1)


def _load_config() -> dict:
    """Load APT config / APT 설정 로드."""
    if not APT_CONFIG_FILE.exists():
        return {"REPO_CODENAME": "stable"}
    
    config = {}
    for line in APT_CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"')
    return config


def _save_config(config: dict) -> None:
    """Save APT config / APT 설정 저장."""
    lines = []
    for key, value in config.items():
        lines.append(f'{key}="{value}"')
    APT_CONFIG_FILE.write_text("\n".join(lines) + "\n")


def _check_gh_installed() -> bool:
    """Check if GitHub CLI is installed / GitHub CLI 설치 여부 확인."""
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _check_gh_authenticated() -> tuple[bool, str]:
    """Check if GitHub CLI is authenticated / GitHub CLI 인증 여부 확인.
    
    Returns:
        tuple: (is_authenticated, error_message)
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, result.stderr.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return False, str(e)


def _get_installed_version(package: str, codename: str) -> Optional[str]:
    """Get currently installed package version / 현재 설치된 패키지 버전 확인."""
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    result = subprocess.run(
        ["reprepro", "-b", str(APT_REPO), "list", codename],
        capture_output=True,
        text=True,
    )
    
    for line in result.stdout.strip().splitlines():
        if package in line:
            # Format: codename|component|arch: package version
            parts = line.split()
            if len(parts) >= 2:
                return parts[-1]  # version is last part
    return None


def _get_github_latest_release(repo: str) -> Optional[dict]:
    """Get latest release info from GitHub / GitHub에서 최신 릴리스 정보 가져오기."""
    try:
        result = subprocess.run(
            ["gh", "release", "list", "-R", repo, "--limit", "1", "--json", "tagName,name,publishedAt,isLatest"],
            capture_output=True,
            text=True,
        )
        
        # Handle specific exit codes
        if result.returncode == 4:
            # Exit code 4: authentication required
            console.print("[red]✗[/red] GitHub CLI authentication required.")
            console.print("  [dim]gh is installed but not authenticated for this user.[/dim]")
            console.print("")
            console.print("  If running with sudo, authenticate as root:")
            console.print("    [cyan]sudo gh auth login[/cyan]")
            console.print("")
            console.print("  Or pass your token via environment variable:")
            console.print("    [cyan]sudo GH_TOKEN=$(gh auth token) vaultctl repo sync[/cyan]")
            return None
        elif result.returncode != 0:
            console.print(f"[red]✗[/red] GitHub CLI error (exit code {result.returncode})")
            if result.stderr:
                console.print(f"  {result.stderr.strip()}")
            return None
        
        releases = json.loads(result.stdout)
        if releases:
            return releases[0]
        return None
    except json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] Failed to parse release info: {e}")
        return None
    except FileNotFoundError:
        console.print("[red]✗[/red] GitHub CLI (gh) not found.")
        return None


def _download_deb_from_release(repo: str, tag: str, dest_dir: Path) -> Optional[Path]:
    """Download .deb file from GitHub release / GitHub 릴리스에서 .deb 파일 다운로드."""
    try:
        result = subprocess.run(
            ["gh", "release", "download", tag, "-R", repo, "--pattern", "*.deb", "-D", str(dest_dir)],
            capture_output=True,
            text=True,
        )
        
        # Handle specific exit codes
        if result.returncode == 4:
            console.print("[red]✗[/red] GitHub CLI authentication required for download.")
            console.print("  [cyan]sudo gh auth login[/cyan]")
            console.print("  or: [cyan]sudo GH_TOKEN=$(gh auth token) vaultctl repo sync[/cyan]")
            return None
        elif result.returncode != 0:
            console.print(f"[red]✗[/red] Download failed (exit code {result.returncode})")
            if result.stderr:
                console.print(f"  {result.stderr.strip()}")
            return None
        
        # Find downloaded deb file / 다운로드된 deb 파일 찾기
        for f in dest_dir.iterdir():
            if f.suffix == ".deb":
                return f
        return None
    except FileNotFoundError:
        console.print("[red]✗[/red] GitHub CLI (gh) not found.")
        return None


@app.command("add")
def add_package(
    deb_file: Path = typer.Argument(..., help="Path to .deb package file"),
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """Add a package to the repository.
    저장소에 패키지 추가.

    Examples:
        vaultctl repo add vaultctl_0.1.0_amd64.deb
        vaultctl repo add package.deb --codename stable
    """
    _check_repo_exists()
    
    if not deb_file.exists():
        console.print(f"[red]✗[/red] File not found: {deb_file}")
        raise typer.Exit(1)
    
    if not str(deb_file).endswith(".deb"):
        console.print("[red]✗[/red] File must be a .deb package")
        raise typer.Exit(1)
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    
    # Set GPG home / GPG 홈 설정
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    # Show package info / 패키지 정보 표시
    console.print(f"[bold]Adding package: {deb_file.name}[/bold]")
    
    result = subprocess.run(
        ["dpkg-deb", "--info", str(deb_file)],
        capture_output=True,
        text=True,
    )
    
    for line in result.stdout.splitlines():
        if any(field in line for field in ["Package:", "Version:", "Architecture:"]):
            console.print(f"  {line.strip()}")
    
    console.print(f"  Target: {codename}")
    console.print()
    
    # Add to repository / 저장소에 추가
    try:
        subprocess.run(
            ["reprepro", "-b", str(APT_REPO), "includedeb", codename, str(deb_file)],
            check=True,
        )
        console.print("[green]✓[/green] Package added successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to add package: {e}")
        raise typer.Exit(1)


@app.command("remove")
def remove_package(
    package: str = typer.Argument(..., help="Package name to remove"),
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """Remove a package from the repository.
    저장소에서 패키지 제거.

    Examples:
        vaultctl repo remove vaultctl
        vaultctl repo remove vaultctl --codename stable
    """
    _check_repo_exists()
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print(f"[bold]Removing package: {package}[/bold]")
    console.print(f"  From: {codename}")
    
    try:
        subprocess.run(
            ["reprepro", "-b", str(APT_REPO), "remove", codename, package],
            check=True,
        )
        console.print("[green]✓[/green] Package removed successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to remove package: {e}")
        raise typer.Exit(1)


@app.command("list")
def list_packages(
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """List packages in the repository.
    저장소의 패키지 목록.

    Examples:
        vaultctl repo list
        vaultctl repo list --codename stable
    """
    _check_repo_exists()
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print(f"[bold]Packages in {codename}[/bold]\n")
    
    result = subprocess.run(
        ["reprepro", "-b", str(APT_REPO), "list", codename],
        capture_output=True,
        text=True,
    )
    
    if not result.stdout.strip():
        console.print("[dim]No packages found[/dim]")
        return
    
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Codename")
    table.add_column("Component")
    table.add_column("Arch")
    table.add_column("Package")
    table.add_column("Version")
    
    for line in result.stdout.strip().splitlines():
        # Format: codename|component|arch: package version
        if "|" in line and ":" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                code = parts[0]
                comp = parts[1]
                rest = parts[2].split(":")
                arch = rest[0].strip() if rest else ""
                pkg_info = rest[1].strip() if len(rest) > 1 else ""
                pkg_parts = pkg_info.split()
                pkg_name = pkg_parts[0] if pkg_parts else ""
                pkg_ver = pkg_parts[1] if len(pkg_parts) > 1 else ""
                table.add_row(code, comp, arch, pkg_name, pkg_ver)
    
    console.print(table)


@app.command("info")
def repo_info():
    """Show repository information.
    저장소 정보 표시.

    Examples:
        vaultctl repo info
    """
    _check_repo_exists()
    
    config = _load_config()
    
    console.print(Panel.fit(
        "[bold blue]APT Repository Information[/bold blue]",
        title="📦 Repository Info",
    ))
    
    # Basic info / 기본 정보
    table = Table(show_header=False, box=None)
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    
    table.add_row("URL", f"https://{config.get('DOMAIN', 'N/A')}")
    table.add_row("Repository Path", str(APT_REPO))
    table.add_row("Codename", config.get("REPO_CODENAME", "stable"))
    table.add_row("Web Server", config.get("WEB_SERVER", "N/A").upper())
    
    if config.get("WEB_SERVER") == "traefik":
        # Get local IP / 로컬 IP 가져오기
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
        )
        local_ip = result.stdout.split()[0] if result.stdout else "N/A"
        table.add_row("Internal", f"{local_ip}:{config.get('LISTEN_PORT', '80')}")
    
    console.print(table)
    
    # Auth info / 인증 정보
    if config.get("ENABLE_AUTH") == "true":
        console.print("\n[bold]Authentication[/bold]")
        console.print(f"  Username: {config.get('AUTH_USER', 'N/A')}")
        console.print(f"  Password: {config.get('AUTH_PASS', '****')}")
    
    # Package list / 패키지 목록
    console.print("\n[bold]Packages[/bold]")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    result = subprocess.run(
        ["reprepro", "-b", str(APT_REPO), "list", config.get("REPO_CODENAME", "stable")],
        capture_output=True,
        text=True,
    )
    
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            console.print(f"  {line}")
    else:
        console.print("  [dim]No packages[/dim]")
    
    # Client setup command / 클라이언트 설정 명령어
    console.print("\n[bold]Client Setup Command[/bold]")
    domain = config.get("DOMAIN", "apt.example.com")
    if config.get("ENABLE_AUTH") == "true":
        console.print(f"  curl -fsSL https://{domain}/setup-client.sh | sudo bash -s -- {config.get('AUTH_USER', 'USER')} 'PASSWORD'")
    else:
        console.print(f"  curl -fsSL https://{domain}/setup-client.sh | sudo bash")


@app.command("export")
def export_repo():
    """Re-export repository (regenerate metadata).
    저장소 재내보내기 (메타데이터 재생성).

    Use after manual changes to the repository.
    저장소를 수동으로 변경한 후 사용합니다.
    """
    _check_repo_exists()
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print("[bold]Exporting repository...[/bold]")
    
    try:
        subprocess.run(
            ["reprepro", "-b", str(APT_REPO), "export"],
            check=True,
        )
        console.print("[green]✓[/green] Repository exported successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to export: {e}")
        raise typer.Exit(1)


@app.command("check")
def check_repo():
    """Check repository integrity.
    저장소 무결성 검사.
    """
    _check_repo_exists()
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print("[bold]Checking repository integrity...[/bold]")
    
    try:
        subprocess.run(
            ["reprepro", "-b", str(APT_REPO), "check"],
            check=True,
        )
        console.print("[green]✓[/green] Repository integrity OK")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Integrity check failed: {e}")
        raise typer.Exit(1)


@app.command("clean")
def clean_repo(
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """Clean up old/unused files from repository.
    저장소에서 오래된/미사용 파일 정리.
    """
    _check_repo_exists()
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print(f"[bold]Cleaning repository ({codename})...[/bold]")
    
    try:
        subprocess.run(
            ["reprepro", "-b", str(APT_REPO), "deleteunreferenced"],
            check=True,
        )
        console.print("[green]✓[/green] Repository cleaned")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Cleanup failed: {e}")
        raise typer.Exit(1)


@app.command("sync")
def sync_github(
    check_only: bool = typer.Option(False, "--check", "-c", help="Check for updates only, don't deploy"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deploy even if version exists"),
    package: Optional[str] = typer.Option(None, "--package", "-p", help="Package name to check (default: from deb filename)"),
):
    """Sync latest release from GitHub to APT repository.
    GitHub의 최신 릴리스를 APT 저장소에 동기화.

    Requires: GitHub CLI (gh) installed and authenticated.
    필요: GitHub CLI (gh) 설치 및 인증 완료.

    Examples:
        vaultctl repo sync              # Download and deploy latest release
        vaultctl repo sync --check      # Check for updates only
        vaultctl repo sync --force      # Force deploy even if exists
    """
    _check_repo_exists()
    
    # Check gh CLI / gh CLI 확인
    if not _check_gh_installed():
        console.print("[red]✗[/red] GitHub CLI (gh) is not installed.")
        console.print("  Install: https://cli.github.com/")
        console.print("  Ubuntu: sudo apt install gh")
        raise typer.Exit(1)
    
    # Load config / 설정 로드
    config = _load_config()
    github_repo = config.get("GITHUB_REPO")
    
    if not github_repo:
        console.print("[red]✗[/red] GitHub repository not configured.")
        console.print("  Run: vaultctl repo config --github-repo owner/repo")
        raise typer.Exit(1)
    
    codename = config.get("REPO_CODENAME", "stable")
    
    console.print(f"[bold]Checking GitHub releases...[/bold]")
    console.print(f"  Repository: {github_repo}")
    
    # Get latest release / 최신 릴리스 확인
    release = _get_github_latest_release(github_repo)
    if not release:
        console.print("[red]✗[/red] No releases found.")
        raise typer.Exit(1)
    
    tag = release.get("tagName", "")
    release_name = release.get("name", tag)
    
    # Extract version from tag (remove 'v' prefix if present)
    github_version = tag.lstrip("v")
    
    console.print(f"  Latest release: {release_name} ({tag})")
    console.print(f"  Published: {release.get('publishedAt', 'N/A')[:10]}")
    
    # Check current version / 현재 버전 확인
    pkg_name = package or github_repo.split("/")[-1]  # Default to repo name
    current_version = _get_installed_version(pkg_name, codename)
    
    if current_version:
        console.print(f"  Current version: {current_version}")
        
        if current_version == github_version and not force:
            console.print("\n[green]✓[/green] Already up to date.")
            return
        elif current_version == github_version and force:
            console.print("\n[yellow]![/yellow] Same version, forcing deploy...")
    else:
        console.print(f"  Current version: [dim]not installed[/dim]")
    
    if check_only:
        if current_version != github_version:
            console.print(f"\n[yellow]![/yellow] New version available: {github_version}")
            console.print("  Run without --check to deploy.")
        return
    
    # Download and deploy / 다운로드 및 배포
    console.print(f"\n[bold]Downloading release {tag}...[/bold]")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        deb_file = _download_deb_from_release(github_repo, tag, tmppath)
        
        if not deb_file:
            console.print("[red]✗[/red] No .deb file found in release.")
            raise typer.Exit(1)
        
        console.print(f"  Downloaded: {deb_file.name}")
        
        # Add to repository / 저장소에 추가
        console.print(f"\n[bold]Deploying to APT repository...[/bold]")
        os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
        
        try:
            subprocess.run(
                ["reprepro", "-b", str(APT_REPO), "includedeb", codename, str(deb_file)],
                check=True,
            )
            console.print(f"[green]✓[/green] Successfully deployed {deb_file.name}")
            console.print(f"\n  Clients can update with:")
            console.print(f"    sudo apt update && sudo apt upgrade {pkg_name}")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to deploy: {e}")
            raise typer.Exit(1)


@app.command("config")
def repo_config(
    github_repo: Optional[str] = typer.Option(None, "--github-repo", "-g", help="Set GitHub repository (owner/repo)"),
    show: bool = typer.Option(False, "--show", "-s", help="Show current configuration"),
):
    """Configure APT repository settings.
    APT 저장소 설정 관리.

    Examples:
        vaultctl repo config                           # Show current config
        vaultctl repo config --github-repo owner/repo  # Set GitHub repository
        vaultctl repo config -g harmonys-app/vaultctl  # Set GitHub repository
    """
    _check_repo_exists()
    
    config = _load_config()
    
    # Set GitHub repository / GitHub 저장소 설정
    if github_repo:
        if "/" not in github_repo:
            console.print("[red]✗[/red] Invalid format. Use: owner/repo")
            console.print("  Example: harmonys-app/vaultctl")
            raise typer.Exit(1)
        
        config["GITHUB_REPO"] = github_repo
        _save_config(config)
        console.print(f"[green]✓[/green] GitHub repository set: {github_repo}")
        return
    
    # Show configuration / 설정 표시
    console.print(Panel.fit(
        "[bold blue]APT Repository Configuration[/bold blue]",
        title="⚙️  Config",
    ))
    
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Setting")
    table.add_column("Value")
    
    table.add_row("Domain", config.get("DOMAIN", "[dim]not set[/dim]"))
    table.add_row("Codename", config.get("REPO_CODENAME", "stable"))
    table.add_row("Web Server", config.get("WEB_SERVER", "[dim]not set[/dim]").upper())
    table.add_row("GitHub Repository", config.get("GITHUB_REPO", "[dim]not set[/dim]"))
    table.add_row("Auth Enabled", config.get("ENABLE_AUTH", "false"))
    
    if config.get("ENABLE_AUTH") == "true":
        table.add_row("Auth User", config.get("AUTH_USER", "[dim]not set[/dim]"))
    
    console.print(table)
    
    # Show sync command hint if GitHub repo is set / GitHub 저장소 설정 시 sync 명령어 힌트
    if config.get("GITHUB_REPO"):
        console.print("\n[dim]To sync latest release:[/dim]")
        console.print("  vaultctl repo sync")
    else:
        console.print("\n[dim]To enable GitHub sync:[/dim]")
        console.print("  vaultctl repo config --github-repo owner/repo")
