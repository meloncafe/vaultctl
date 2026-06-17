"""Self-update / version commands for vaultctl.

Mirrors dctl's `version` / `self-update` UX, adapted to vaultctl's actual
distribution. vaultctl is a public repo that ships two supported install
paths, and self-update handles both:

    - apt:    installed from an APT repository (`apt install vaultctl`); the
              native update is `apt-get install --only-upgrade vaultctl`.
    - github: installed from a GitHub release .deb via scripts/install.sh; the
              update is "download the latest release .deb and install it".

`self-update --method auto` (default) prefers the native apt path when an APT
repo offers a newer candidate, otherwise falls back to the GitHub release.

Design notes:
    - `version` does a best-effort, network-tolerant update check. It never
      hangs or errors on offline/unreachable networks (short timeout, all
      failures swallowed) — like dctl's `version`.
    - `self-update` is explicit: it reports a real error if it cannot resolve a
      target, download the asset, or run apt.
    - Stdlib only (urllib/json/subprocess); no new dependencies.
    - Public repo, so no token is needed. A GITHUB_TOKEN, if set, is used only
      to relax API rate limits.
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


# ── version helpers ──────────────────────────────────────────────────────────

def _github_token() -> Optional[str]:
    """Optional GitHub token — only to relax API rate limits (repo is public)."""
    return os.environ.get("VAULTCTL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _request(url: str, accept: str) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", f"vaultctl/{__version__}")
    token = _github_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _parse_version(v: str) -> tuple:
    """Best-effort version tuple for comparison.

    Trims a leading 'v', drops build metadata, and treats a pre-release suffix
    (e.g. '-dev', '-rc1') as *older* than the same numeric release. Debian
    package revisions (e.g. '0.6.0-1') are compared on their upstream part; for
    exact apt decisions we defer to apt itself, not this parser.
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
    return (parts[0], parts[1], parts[2], -is_pre)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def latest_version(timeout: int = _CHECK_TIMEOUT) -> Optional[str]:
    """Latest GitHub release version (tag without leading 'v'), or None.

    Never raises — safe to call from `version`. Returns None when offline, when
    there are no releases, or on any HTTP/parse error.
    """
    try:
        req = _request(_API_LATEST, "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").lstrip("v")
        return tag or None
    except Exception:
        return None


def _apt_installed_candidate() -> tuple:
    """Return (installed, candidate) from `apt-cache policy vaultctl`.

    Either may be None (package unknown to apt, or no candidate). Never raises.
    """
    if not shutil.which("apt-cache"):
        return None, None
    try:
        out = subprocess.run(
            ["apt-cache", "policy", PACKAGE],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None, None
    if out.returncode != 0:
        return None, None

    installed = candidate = None
    for line in out.stdout.splitlines():
        s = line.strip()
        if s.startswith("Installed:"):
            val = s.split(":", 1)[1].strip()
            installed = None if val in ("(none)", "") else val
        elif s.startswith("Candidate:"):
            val = s.split(":", 1)[1].strip()
            candidate = None if val in ("(none)", "") else val
    return installed, candidate


# ── commands ─────────────────────────────────────────────────────────────────

def version_command():
    """Print the version, and whether an update is available (if reachable)."""
    console.print(f"vaultctl {__version__}")
    latest = latest_version()
    if latest and _is_newer(latest, __version__):
        console.print(
            f"[yellow]![/yellow] an update is available "
            f"({__version__} → {latest}) — run 'vaultctl self-update'"
        )


def _sudo_prefix() -> list:
    """[] when root, ['sudo'] otherwise; aborts if neither is possible."""
    if os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        console.print("[red]✗[/red] Root privileges required. Re-run as root, or install sudo.")
        raise typer.Exit(1)
    return ["sudo"]


def _update_via_apt(candidate: Optional[str]):
    """Native apt upgrade path (APT-repo installs)."""
    if not shutil.which("apt-get"):
        console.print("[red]✗[/red] apt-get not found — the apt method needs a Debian-family host.")
        raise typer.Exit(1)

    prefix = _sudo_prefix()
    console.print("[dim]Refreshing apt and upgrading vaultctl...[/dim]")
    if subprocess.run(prefix + ["apt-get", "update"]).returncode != 0:
        console.print("[yellow]![/yellow] apt-get update reported an error; continuing.")
    result = subprocess.run(prefix + ["apt-get", "install", "--only-upgrade", "-y", PACKAGE])
    if result.returncode != 0:
        console.print(f"[red]✗[/red] apt-get install failed (exit {result.returncode}).")
        raise typer.Exit(1)
    tgt = f" ({candidate})" if candidate else ""
    console.print(f"[green]✓[/green] apt upgrade complete{tgt}. Run 'vaultctl version' to confirm.")


def _update_via_github(target: str):
    """GitHub-release path: download the .deb and install it (like install.sh)."""
    if not shutil.which("apt-get"):
        console.print("[red]✗[/red] apt-get not found — installing a release .deb needs a Debian-family host.")
        console.print("  Reinstall from source (poetry/pip) instead.")
        raise typer.Exit(1)

    deb_name = f"{PACKAGE}_{target}_{ARCH}.deb"
    url = f"https://github.com/{REPO}/releases/download/v{target}/{deb_name}"

    tmpdir = tempfile.mkdtemp(prefix="vaultctl-update-")
    deb_path = os.path.join(tmpdir, deb_name)
    try:
        console.print(f"[dim]Downloading {deb_name}...[/dim]")
        try:
            req = _request(url, "application/octet-stream")
            with urllib.request.urlopen(req, timeout=60) as resp, open(deb_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        except urllib.error.HTTPError as e:
            console.print(f"[red]✗[/red] Download failed (HTTP {e.code}): {url}")
            if e.code == 404:
                console.print(f"  No release asset '{deb_name}'. Check the version or the release.")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]✗[/red] Download failed: {e}")
            raise typer.Exit(1)

        try:
            os.chmod(deb_path, 0o644)
        except OSError:
            pass

        prefix = _sudo_prefix()
        console.print("[dim]Installing package...[/dim]")
        result = subprocess.run(prefix + ["apt-get", "install", "-y", deb_path])
        if result.returncode != 0:
            console.print(f"[red]✗[/red] apt-get install failed (exit {result.returncode}).")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Updated to vaultctl {target}.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def self_update_command(
    method: str = typer.Option("auto", "--method", "-m", help="Update source: auto | apt | github"),
    check: bool = typer.Option(False, "--check", help="Only check for an update; do not install"),
    target_version: Optional[str] = typer.Option(None, "--version", help="Install a specific version (github method)"),
):
    """Update vaultctl to the latest release.

    auto (default) uses the native apt upgrade when an APT repo offers a newer
    version, otherwise downloads the latest GitHub release .deb. Force a path
    with --method apt|github. --version pins a specific GitHub release.
    """
    if method not in ("auto", "apt", "github"):
        console.print("[red]✗[/red] --method must be one of: auto, apt, github")
        raise typer.Exit(1)

    # A pinned version is only meaningful for the GitHub release path.
    if target_version:
        method = "github"

    # Resolve what each path could offer.
    gh_target = target_version.lstrip("v") if target_version else latest_version(timeout=10)
    gh_avail = bool(gh_target) and (target_version is not None or _is_newer(gh_target, __version__))

    apt_installed, apt_candidate = _apt_installed_candidate()
    apt_avail = apt_candidate is not None and apt_candidate != apt_installed

    # Decide the path.
    if method == "apt":
        if apt_installed is None:
            console.print("[red]✗[/red] vaultctl is not known to apt (not installed from an APT repo).")
            console.print("  Use --method github, or reinstall from source.")
            raise typer.Exit(1)
        if not apt_avail:
            console.print(f"[green]✓[/green] Already up to date via apt (vaultctl {apt_installed}).")
            return
        if check:
            console.print(f"[yellow]![/yellow] apt update available: {apt_installed} → {apt_candidate}")
            return
        _update_via_apt(apt_candidate)
        return

    if method == "github":
        if not gh_target:
            console.print("[red]✗[/red] Could not resolve a GitHub release (offline or no releases).")
            console.print("  APT-repo installs: vaultctl self-update --method apt")
            raise typer.Exit(1)
        if not gh_avail:
            console.print(f"[green]✓[/green] Already up to date (vaultctl {__version__}).")
            return
        if check:
            console.print(f"[yellow]![/yellow] github update available: {__version__} → {gh_target}")
            return
        _update_via_github(gh_target)
        return

    # method == auto: prefer the native apt upgrade, else GitHub release.
    if apt_avail:
        if check:
            console.print(f"[yellow]![/yellow] apt update available: {apt_installed} → {apt_candidate}")
            return
        _update_via_apt(apt_candidate)
        return
    if gh_avail:
        if check:
            console.print(f"[yellow]![/yellow] github update available: {__version__} → {gh_target}")
            return
        _update_via_github(gh_target)
        return

    console.print(f"[green]✓[/green] Already up to date (vaultctl {__version__}).")
