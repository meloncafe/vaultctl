"""Self-update / version commands for vaultctl.

Mirrors dctl's `version` / `self-update` UX, adapted to vaultctl's actual
distribution model. dctl is a git checkout and self-updates with
`git pull` + re-running its installer; vaultctl ships as a .deb published
as a GitHub release and installed with apt (see scripts/install.sh), so the
equivalent here is: check the latest release, then download that release's
.deb and install it.

Design notes:
    - `version` does a best-effort, network-tolerant update check. It never
      hangs or errors on offline/unreachable networks (short timeout, all
      failures swallowed) — exactly like dctl's `version`.
    - `self-update` is explicit: it reports a real error if it cannot reach
      GitHub, download the asset, or run apt.
    - Only stdlib is used (urllib/json/subprocess) so this keeps working even
      when optional runtime deps are unavailable.
    - vaultctl may be a private repo; release API + asset download then need a
      token. VAULTCTL_GITHUB_TOKEN or GITHUB_TOKEN is sent as a Bearer token
      when present. APT-server installs can alternatively use
      `apt install --only-upgrade vaultctl`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Optional

import typer
from rich.console import Console

from vaultctl import __version__

console = Console()

REPO = os.environ.get("VAULTCTL_REPO", "meloncafe/vaultctl")
PACKAGE = "vaultctl"
ARCH = "amd64"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# Keep the check timeout short so an unreachable network (offline, intranet
# with no route to GitHub) fails fast and silently rather than hanging.
_CHECK_TIMEOUT = 5


def _github_token() -> Optional[str]:
    """Optional GitHub token for private-repo release access."""
    return os.environ.get("VAULTCTL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _request(url: str, timeout: int, accept: str) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", f"vaultctl/{__version__}")
    token = _github_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _parse_version(v: str) -> tuple:
    """Best-effort version tuple for comparison.

    Trims a leading 'v', drops build metadata, and treats any pre-release
    suffix (e.g. '-dev', '-rc1') as *older* than the same numeric release.
    """
    v = v.strip().lstrip("v")
    core = v.split("-")[0].split("+")[0]
    parts = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    is_pre = 1 if "-" in v else 0
    # -is_pre makes a pre-release sort below the plain release with same numbers
    return (parts[0], parts[1], parts[2], -is_pre)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def latest_version(timeout: int = _CHECK_TIMEOUT) -> Optional[str]:
    """Latest release version (tag without leading 'v'), or None on any failure.

    Never raises — safe to call from `version`. Returns None when offline, when
    there are no releases, or on any HTTP/parse error.
    """
    try:
        req = _request(_API_LATEST, timeout, "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").lstrip("v")
        return tag or None
    except Exception:
        return None


def version_command():
    """Print the version, and whether an update is available (if reachable)."""
    console.print(f"vaultctl {__version__}")
    latest = latest_version()
    if latest and _is_newer(latest, __version__):
        console.print(
            f"[yellow]![/yellow] an update is available "
            f"({__version__} → {latest}) — run 'vaultctl self-update'"
        )


def self_update_command(
    check: bool = typer.Option(False, "--check", help="Only check for an update; do not install"),
    target_version: Optional[str] = typer.Option(None, "--version", help="Install a specific version instead of the latest"),
):
    """Update vaultctl to the latest release.

    Downloads the release .deb and installs it with apt (re-using the same
    flow as scripts/install.sh). Use --check to only report, or --version to
    pin a specific release.
    """
    current = __version__
    target = target_version.lstrip("v") if target_version else latest_version(timeout=10)

    if not target:
        console.print("[red]✗[/red] Could not determine the latest release.")
        console.print("  • Offline / no releases / private repo without a token.")
        console.print("  • Private repo: set GITHUB_TOKEN (repo read access) and retry.")
        console.print("  • APT-server installs: sudo apt update && sudo apt install --only-upgrade vaultctl")
        raise typer.Exit(1)

    # Without an explicit --version, skip when already current.
    if target_version is None and not _is_newer(target, current):
        console.print(f"[green]✓[/green] Already up to date (vaultctl {current}).")
        return

    console.print(f"[dim]Current: {current}  →  Target: {target}[/dim]")

    if check:
        console.print(f"[yellow]![/yellow] Update available: {target} (run without --check to install)")
        return

    # vaultctl is distributed as a .deb; updating needs a Debian-family host.
    if not shutil.which("apt-get"):
        console.print("[red]✗[/red] apt-get not found — self-update installs a .deb and needs a Debian-family system.")
        console.print("  Reinstall from source (poetry/pip) or via your package manager instead.")
        raise typer.Exit(1)

    deb_name = f"{PACKAGE}_{target}_{ARCH}.deb"
    url = f"https://github.com/{REPO}/releases/download/v{target}/{deb_name}"

    tmpdir = tempfile.mkdtemp(prefix="vaultctl-update-")
    deb_path = os.path.join(tmpdir, deb_name)
    try:
        console.print(f"[dim]Downloading {deb_name}...[/dim]")
        try:
            req = _request(url, timeout=60, accept="application/octet-stream")
            with urllib.request.urlopen(req, timeout=60) as resp, open(deb_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        except urllib.error.HTTPError as e:
            console.print(f"[red]✗[/red] Download failed (HTTP {e.code}): {url}")
            if e.code in (401, 403, 404):
                console.print("  If this is a private repo, set GITHUB_TOKEN with repo access and retry.")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]✗[/red] Download failed: {e}")
            raise typer.Exit(1)

        try:
            os.chmod(deb_path, 0o644)
        except OSError:
            pass

        cmd = ["apt-get", "install", "-y", deb_path]
        if os.geteuid() != 0:
            if not shutil.which("sudo"):
                console.print("[red]✗[/red] Root privileges required to install. Re-run as root, or install sudo.")
                raise typer.Exit(1)
            cmd = ["sudo"] + cmd

        console.print("[dim]Installing package...[/dim]")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            console.print(f"[red]✗[/red] apt-get install failed (exit {result.returncode}).")
            raise typer.Exit(1)

        console.print(f"[green]✓[/green] Updated to vaultctl {target}.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
