"""Self-update / version commands for vaultctl.

Ported from dctl's git-based self-update, adapted to vaultctl's source install.
scripts/install.sh clones the repo into ~/.local/share/vaultctl and installs it
editable into a venv; `self-update` fast-forwards that checkout (with the same
uncommitted / diverged / fast-forward safety as dctl) and reinstalls. When
vaultctl was installed from an APT repository instead (no source checkout), it
falls back to `apt-get --only-upgrade`.

Design notes:
    - `version` does a best-effort, network-tolerant update check: a short
      `git fetch` and a commits-behind count, silent on offline/unreachable —
      like dctl's `version`/`update_count`.
    - Uses existing CLI dependencies (typer, rich); no *new* Python dependencies
      beyond vaultctl's existing stack.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

import vaultctl
from vaultctl import __version__

console = Console()

PACKAGE = "vaultctl"
_FETCH_TIMEOUT = 5  # seconds; keep the check fast so offline never hangs
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never prompt for creds


# ── checkout / git helpers ───────────────────────────────────────────────────

def _checkout_dir() -> Optional[Path]:
    """The git checkout backing the running vaultctl, or None.

    For a source install (scripts/install.sh, editable) vaultctl.__file__ lives
    under the cloned tree, so a `.git` ancestor exists. For an apt/.deb install
    it lives in site-packages with no `.git` above it → None.
    """
    try:
        start = Path(vaultctl.__file__).resolve()
    except Exception:
        return None
    for parent in start.parents:
        if (parent / ".git").is_dir():
            return parent
    return None


def _git(cwd: Path, *args: str, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=timeout, env=_GIT_ENV,
    )


def _git_out(cwd: Path, *args: str) -> str:
    r = _git(cwd, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def _current_branch(cwd: Path) -> Optional[str]:
    return _git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD") or None


def _commits_behind(cwd: Path, timeout: int = _FETCH_TIMEOUT) -> Optional[int]:
    """How many commits upstream is ahead. None on any failure/offline (silent)."""
    if not shutil.which("git"):
        return None
    branch = _current_branch(cwd)
    if not branch:
        return None
    try:
        f = _git(cwd, "fetch", "--quiet", "origin", branch, timeout=timeout)
    except Exception:
        return None
    if f.returncode != 0:
        return None
    count = _git_out(cwd, "rev-list", "--count", f"HEAD..origin/{branch}")
    return int(count) if count.isdigit() else None


# ── commands ─────────────────────────────────────────────────────────────────

def version_command():
    """Print the version, and whether an update is available (if reachable)."""
    checkout = _checkout_dir()
    if checkout:
        sha = _git_out(checkout, "rev-parse", "--short", "HEAD")
        console.print(f"vaultctl {__version__}" + (f" ({sha})" if sha else ""))
        behind = _commits_behind(checkout)
        if behind:
            console.print(
                f"[yellow]![/yellow] an update is available "
                f"({behind} commit(s) behind) — run 'vaultctl self-update'"
            )
    else:
        console.print(f"vaultctl {__version__}")


def _sudo_prefix() -> list:
    if os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        console.print("[red]✗[/red] Root privileges required. Re-run as root, or install sudo.")
        raise typer.Exit(1)
    return ["sudo"]


def _update_via_git(checkout: Path, check: bool):
    """Fast-forward the source checkout and reinstall (dctl-style)."""
    if not shutil.which("git"):
        console.print("[red]✗[/red] git not found — required to update a source checkout.")
        raise typer.Exit(1)

    # Ignore exec-bit (chmod) diffs so file-mode changes never block the merge.
    _git(checkout, "config", "core.fileMode", "false")

    console.print("[dim]Checking for updates…[/dim]")
    branch = _current_branch(checkout)
    if not branch:
        console.print("[red]✗[/red] Could not determine the current branch.")
        raise typer.Exit(1)
    upstream = f"origin/{branch}"

    f = _git(checkout, "fetch", "--quiet", "origin", branch, timeout=30)
    if f.returncode != 0:
        console.print(f"[red]✗[/red] Could not reach origin: {f.stderr.strip() or 'fetch failed'}")
        raise typer.Exit(1)

    local = _git_out(checkout, "rev-parse", "HEAD")
    remote = _git_out(checkout, "rev-parse", upstream)
    if not remote:
        console.print(f"[red]✗[/red] No upstream '{upstream}'. Reinstall via scripts/install.sh.")
        raise typer.Exit(1)
    if local == remote:
        console.print(f"[green]✓[/green] Already up to date (vaultctl {__version__}).")
        return

    behind = _git_out(checkout, "rev-list", "--count", f"HEAD..{upstream}")
    if check:
        console.print(f"[yellow]![/yellow] Update available: {behind} commit(s) behind on {branch}.")
        return

    # Uncommitted local changes block any update path; surface them first.
    if _git_out(checkout, "status", "--porcelain"):
        console.print(f"[red]✗[/red] Uncommitted changes in {checkout}.")
        console.print(f"  Inspect: git -C {checkout} status")
        console.print(f"  Discard: git -C {checkout} checkout -- .   then retry")
        raise typer.Exit(1)

    base = _git_out(checkout, "merge-base", "HEAD", upstream)
    if base == local:
        console.print(f"[dim]Applying {behind} new commit(s)…[/dim]")
        m = _git(checkout, "merge", "--ff-only", upstream, "--quiet")
        if m.returncode != 0:
            console.print(f"[red]✗[/red] Fast-forward failed: {m.stderr.strip()}")
            raise typer.Exit(1)
    elif base == remote:
        console.print(f"[red]✗[/red] {checkout} has local commits not on {upstream}.")
        console.print(f"  To discard them: git -C {checkout} reset --hard {upstream}")
        raise typer.Exit(1)
    else:
        console.print(f"[red]✗[/red] Local history diverged from {upstream} (force-push?).")
        console.print(f"  To match upstream: git -C {checkout} reset --hard {upstream}")
        raise typer.Exit(1)

    # Reinstall into the running venv to pick up dependency / entry-point changes.
    console.print("[dim]Reinstalling…[/dim]")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(checkout)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        console.print("[red]✗[/red] Reinstall failed:")
        console.print((r.stderr.strip() or r.stdout.strip())[:2000])
        console.print(f"  Recover: re-run scripts/install.sh, or "
                      f"'{sys.executable} -m pip install -e {checkout}'")
        raise typer.Exit(1)

    new_sha = _git_out(checkout, "rev-parse", "--short", "HEAD")
    console.print(f"[green]✓[/green] Updated to {branch}@{new_sha}. Run 'vaultctl version' to confirm.")


def _update_via_apt(check: bool):
    """apt-repo install path: upgrade the package."""
    if not shutil.which("apt-get"):
        console.print("[red]✗[/red] Not a source checkout and apt-get is unavailable — cannot self-update.")
        console.print("  Reinstall via scripts/install.sh.")
        raise typer.Exit(1)

    installed = candidate = None
    if shutil.which("apt-cache"):
        try:
            out = subprocess.run(["apt-cache", "policy", PACKAGE], capture_output=True, text=True, timeout=15)
            for line in out.stdout.splitlines():
                s = line.strip()
                if s.startswith("Installed:"):
                    installed = s.split(":", 1)[1].strip()
                elif s.startswith("Candidate:"):
                    candidate = s.split(":", 1)[1].strip()
        except Exception:
            pass

    if installed in (None, "(none)"):
        console.print("[red]✗[/red] vaultctl is neither a source checkout nor an apt package.")
        console.print("  Reinstall via scripts/install.sh.")
        raise typer.Exit(1)

    if candidate and candidate == installed:
        console.print(f"[green]✓[/green] Already up to date via apt (vaultctl {installed}).")
        return
    if check:
        tgt = f" {installed} → {candidate}" if candidate else ""
        console.print(f"[yellow]![/yellow] apt update available:{tgt} (run without --check to upgrade)")
        return

    prefix = _sudo_prefix()
    console.print("[dim]Upgrading via apt…[/dim]")
    if subprocess.run(prefix + ["apt-get", "update"]).returncode != 0:
        console.print("[yellow]![/yellow] apt-get update reported an error; continuing.")
    r = subprocess.run(prefix + ["apt-get", "install", "--only-upgrade", "-y", PACKAGE])
    if r.returncode != 0:
        console.print(f"[red]✗[/red] apt-get install failed (exit {r.returncode}).")
        raise typer.Exit(1)
    console.print("[green]✓[/green] apt upgrade complete. Run 'vaultctl version' to confirm.")


def self_update_command(
    check: bool = typer.Option(False, "--check", help="Only check for an update; do not install"),
):
    """Update vaultctl to the latest version.

    For a source install (scripts/install.sh) this fast-forwards the checkout
    and reinstalls (dctl-style). For an apt-repo install it runs apt-get
    --only-upgrade instead. The install type is detected automatically.
    """
    checkout = _checkout_dir()
    if checkout:
        _update_via_git(checkout, check)
    else:
        _update_via_apt(check)
