"""APT Repository management commands.
APT 저장소 관리 명령어.

Commands:
    vaultctl admin repo add <file>    # Add package
    vaultctl admin repo remove <pkg>  # Remove package
    vaultctl admin repo list          # List packages
    vaultctl admin repo sync          # Sync from GitHub
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

# Constants
APT_BASE = Path("/var/www/apt")
APT_REPO = APT_BASE / "repo"
APT_GPG_HOME = APT_BASE / ".gnupg"
APT_CONFIG_FILE = APT_BASE / ".config"


def _check_repo_exists() -> None:
    """Check if APT repository is installed."""
    if not APT_REPO.exists():
        console.print("[red]✗[/red] APT repository not installed.")
        console.print("  Run: sudo vaultctl admin setup apt-server")
        raise typer.Exit(1)


def _load_config() -> dict:
    """Load APT config."""
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
    """Save APT config."""
    lines = [f'{key}="{value}"' for key, value in config.items()]
    APT_CONFIG_FILE.write_text("\n".join(lines) + "\n")


def _check_gh_installed() -> bool:
    """Check if GitHub CLI is installed."""
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _get_installed_version(package: str, codename: str) -> Optional[str]:
    """Get currently installed package version."""
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    result = subprocess.run(
        ["reprepro", "-b", str(APT_REPO), "list", codename],
        capture_output=True,
        text=True,
    )
    
    for line in result.stdout.strip().splitlines():
        if package in line:
            parts = line.split()
            if len(parts) >= 2:
                return parts[-1]
    return None


def _get_github_latest_release(repo: str) -> Optional[dict]:
    """Get latest release info from GitHub."""
    try:
        result = subprocess.run(
            ["gh", "release", "list", "-R", repo, "--limit", "1", "--json", "tagName,name,publishedAt,isLatest"],
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 4:
            console.print("[red]✗[/red] GitHub CLI authentication required.")
            console.print("  [cyan]sudo gh auth login[/cyan]")
            return None
        elif result.returncode != 0:
            console.print(f"[red]✗[/red] GitHub CLI error (exit code {result.returncode})")
            return None
        
        releases = json.loads(result.stdout)
        return releases[0] if releases else None
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def _download_deb_from_release(repo: str, tag: str, dest_dir: Path) -> Optional[Path]:
    """Download .deb file from GitHub release."""
    try:
        result = subprocess.run(
            ["gh", "release", "download", tag, "-R", repo, "--pattern", "*.deb", "-D", str(dest_dir)],
            capture_output=True,
            text=True,
        )
        
        if result.returncode != 0:
            console.print(f"[red]✗[/red] Download failed")
            return None
        
        for f in dest_dir.iterdir():
            if f.suffix == ".deb":
                return f
        return None
    except FileNotFoundError:
        return None


@app.command("add")
def add_package(
    deb_file: Path = typer.Argument(..., help="Path to .deb package file"),
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """Add a package to the repository."""
    _check_repo_exists()
    
    if not deb_file.exists():
        console.print(f"[red]✗[/red] File not found: {deb_file}")
        raise typer.Exit(1)
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print(f"[bold]Adding package: {deb_file.name}[/bold]")
    
    result = subprocess.run(["dpkg-deb", "--info", str(deb_file)], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if any(field in line for field in ["Package:", "Version:", "Architecture:"]):
            console.print(f"  {line.strip()}")
    
    try:
        subprocess.run(["reprepro", "-b", str(APT_REPO), "includedeb", codename, str(deb_file)], check=True)
        console.print("[green]✓[/green] Package added successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to add package: {e}")
        raise typer.Exit(1)


@app.command("remove")
def remove_package(
    package: str = typer.Argument(..., help="Package name to remove"),
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """Remove a package from the repository."""
    _check_repo_exists()
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    try:
        subprocess.run(["reprepro", "-b", str(APT_REPO), "remove", codename, package], check=True)
        console.print("[green]✓[/green] Package removed successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to remove package: {e}")
        raise typer.Exit(1)


@app.command("list")
def list_packages(
    codename: Optional[str] = typer.Option(None, "--codename", "-c", help="Target codename"),
):
    """List packages in the repository."""
    _check_repo_exists()
    
    config = _load_config()
    codename = codename or config.get("REPO_CODENAME", "stable")
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    console.print(f"[bold]Packages in {codename}[/bold]\n")
    
    result = subprocess.run(["reprepro", "-b", str(APT_REPO), "list", codename], capture_output=True, text=True)
    
    if not result.stdout.strip():
        console.print("[dim]No packages found[/dim]")
        return
    
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("Arch")
    
    for line in result.stdout.strip().splitlines():
        if "|" in line and ":" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                rest = parts[2].split(":")
                arch = rest[0].strip() if rest else ""
                pkg_info = rest[1].strip() if len(rest) > 1 else ""
                pkg_parts = pkg_info.split()
                pkg_name = pkg_parts[0] if pkg_parts else ""
                pkg_ver = pkg_parts[1] if len(pkg_parts) > 1 else ""
                table.add_row(pkg_name, pkg_ver, arch)
    
    console.print(table)


@app.command("info")
def repo_info():
    """Show repository information."""
    _check_repo_exists()
    
    config = _load_config()
    
    table = Table(show_header=False, box=None)
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    
    table.add_row("URL", f"https://{config.get('DOMAIN', 'N/A')}")
    table.add_row("Codename", config.get("REPO_CODENAME", "stable"))
    table.add_row("GitHub Repo", config.get("GITHUB_REPO", "[dim]not set[/dim]"))
    
    console.print(table)


@app.command("sync")
def sync_github(
    check_only: bool = typer.Option(False, "--check", "-c", help="Check for updates only"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deploy even if version exists"),
    package: Optional[str] = typer.Option(None, "--package", "-p", help="Package name"),
):
    """Sync latest release from GitHub to APT repository."""
    _check_repo_exists()
    
    if not _check_gh_installed():
        console.print("[red]✗[/red] GitHub CLI (gh) is not installed.")
        raise typer.Exit(1)
    
    config = _load_config()
    github_repo = config.get("GITHUB_REPO")
    
    if not github_repo:
        console.print("[red]✗[/red] GitHub repository not configured.")
        console.print("  Run: vaultctl admin repo config --github-repo owner/repo")
        raise typer.Exit(1)
    
    codename = config.get("REPO_CODENAME", "stable")
    
    console.print(f"[bold]Checking GitHub releases...[/bold]")
    console.print(f"  Repository: {github_repo}")
    
    release = _get_github_latest_release(github_repo)
    if not release:
        console.print("[red]✗[/red] No releases found.")
        raise typer.Exit(1)
    
    tag = release.get("tagName", "")
    github_version = tag.lstrip("v")
    
    console.print(f"  Latest: {tag}")
    
    pkg_name = package or github_repo.split("/")[-1]
    current_version = _get_installed_version(pkg_name, codename)
    
    if current_version:
        console.print(f"  Current: {current_version}")
        if current_version == github_version and not force:
            console.print("\n[green]✓[/green] Already up to date.")
            return
    
    if check_only:
        if current_version != github_version:
            console.print(f"\n[yellow]![/yellow] New version available: {github_version}")
        return
    
    console.print(f"\n[bold]Downloading release {tag}...[/bold]")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        deb_file = _download_deb_from_release(github_repo, tag, tmppath)
        
        if not deb_file:
            console.print("[red]✗[/red] No .deb file found in release.")
            raise typer.Exit(1)
        
        console.print(f"  Downloaded: {deb_file.name}")
        
        os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
        
        try:
            subprocess.run(["reprepro", "-b", str(APT_REPO), "includedeb", codename, str(deb_file)], check=True)
            console.print(f"[green]✓[/green] Successfully deployed {deb_file.name}")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to deploy: {e}")
            raise typer.Exit(1)


@app.command("config")
def repo_config(
    github_repo: Optional[str] = typer.Option(None, "--github-repo", "-g", help="Set GitHub repository (owner/repo)"),
):
    """Configure APT repository settings."""
    _check_repo_exists()
    
    config = _load_config()
    
    if github_repo:
        if "/" not in github_repo:
            console.print("[red]✗[/red] Invalid format. Use: owner/repo")
            raise typer.Exit(1)
        
        config["GITHUB_REPO"] = github_repo
        _save_config(config)
        console.print(f"[green]✓[/green] GitHub repository set: {github_repo}")
        return
    
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Setting")
    table.add_column("Value")
    
    table.add_row("Domain", config.get("DOMAIN", "[dim]not set[/dim]"))
    table.add_row("Codename", config.get("REPO_CODENAME", "stable"))
    table.add_row("GitHub Repository", config.get("GITHUB_REPO", "[dim]not set[/dim]"))
    
    console.print(table)


@app.command("export")
def export_repo():
    """Re-export repository (regenerate metadata)."""
    _check_repo_exists()
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    try:
        subprocess.run(["reprepro", "-b", str(APT_REPO), "export"], check=True)
        console.print("[green]✓[/green] Repository exported successfully")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to export: {e}")
        raise typer.Exit(1)


@app.command("clean")
def clean_repo():
    """Clean up unused files from repository."""
    _check_repo_exists()
    os.environ["GNUPGHOME"] = str(APT_GPG_HOME)
    
    try:
        subprocess.run(["reprepro", "-b", str(APT_REPO), "deleteunreferenced"], check=True)
        console.print("[green]✓[/green] Repository cleaned")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Cleanup failed: {e}")
        raise typer.Exit(1)
