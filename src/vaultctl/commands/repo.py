"""APT Repository management commands.
APT 저장소 관리 명령어.
"""

import os
import subprocess
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
