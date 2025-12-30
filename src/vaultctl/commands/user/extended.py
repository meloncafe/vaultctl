"""Extended commands for vaultctl (teller-style).
vaultctl 확장 명령어 (teller 스타일).

User commands:
- vaultctl run: Run with injected env vars
- vaultctl sh: Generate shell export statements
- vaultctl scan: Scan for hardcoded secrets
- vaultctl redact: Mask secrets in logs
- vaultctl watch: Auto-restart on secret change
"""
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console

from vaultctl.config import settings
from vaultctl.vault_client import VaultClient, VaultError

console = Console()


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client."""
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
    """Get secrets."""
    client = _get_authenticated_client()
    try:
        return client.kv_get(settings.kv_mount, settings.get_secret_path(name))
    except VaultError:
        return {}


def _list_secrets() -> list[str]:
    """List secrets."""
    client = _get_authenticated_client()
    try:
        return [k.rstrip("/") for k in client.kv_list(settings.kv_mount, settings.kv_path)]
    except VaultError:
        return []


def run_command(
    name: str = typer.Argument(..., help="Secret name"),
    command: List[str] = typer.Argument(..., help="Command to run"),
    reset: bool = typer.Option(False, "--reset", "-r", help="Reset existing env vars"),
    shell: bool = typer.Option(False, "--shell", "-s", help="Run through shell"),
):
    """Run process with injected environment variables."""
    secrets = _get_secrets(name)
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        raise typer.Exit(1)
    
    if reset:
        env = dict(secrets)
        for key in ["PATH", "HOME", "USER", "SHELL", "TERM"]:
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        env = os.environ.copy()
        env.update(secrets)
    
    console.print(f"[green]▶[/green] Loaded {len(secrets)} environment variables")
    
    if shell:
        result = subprocess.run(" ".join(command), shell=True, env=env)
    else:
        result = subprocess.run(command, env=env)
    
    raise typer.Exit(result.returncode)


def shell_export(
    name: str = typer.Argument(..., help="Secret name"),
    _format: str = typer.Option("bash", "--format", "-f", help="Output format: bash, fish, zsh"),
):
    """Generate shell export statements for eval."""
    secrets = _get_secrets(name)
    if not secrets:
        return
    
    for key, value in secrets.items():
        escaped = str(value).replace("'", "'\"'\"'")
        if _format == "fish":
            print(f"set -gx {key} '{escaped}'")
        else:
            print(f"export {key}='{escaped}'")


def scan_secrets(
    path: Path = typer.Argument(".", help="Path to scan"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Specific secret only"),
    error_if_found: bool = typer.Option(False, "--error-if-found", help="Exit with error if found"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    exclude: List[str] = typer.Option(
        [".git", "node_modules", "__pycache__", ".venv", "venv", ".env"],
        "--exclude", "-e", help="Directories/files to exclude"
    ),
):
    """Scan code for hardcoded secrets from Vault."""
    secrets_to_find = {}
    
    if name:
        data = _get_secrets(name)
        if data:
            for key, value in data.items():
                if len(str(value)) >= 8:
                    secrets_to_find[f"{name}/{key}"] = str(value)
    else:
        for n in _list_secrets():
            data = _get_secrets(n)
            if data:
                for key, value in data.items():
                    if len(str(value)) >= 8:
                        secrets_to_find[f"{n}/{key}"] = str(value)
    
    if not secrets_to_find:
        console.print("[yellow]No secrets to scan for.[/yellow]")
        return
    
    console.print(f"[blue]Scanning...[/blue] {len(secrets_to_find)} secrets, path: {path}")
    
    findings = []
    for file_path in path.rglob("*"):
        if any(ex in str(file_path) for ex in exclude) or not file_path.is_file():
            continue
        try:
            content = file_path.read_text(errors="ignore")
        except Exception:
            continue
        
        for secret_id, secret_value in secrets_to_find.items():
            if secret_value in content:
                for i, line in enumerate(content.split("\n"), 1):
                    if secret_value in line:
                        findings.append({"file": str(file_path), "line": i, "secret": secret_id})
    
    if json_output:
        print(json.dumps(findings, indent=2))
    elif findings:
        console.print(f"\n[red]Found {len(findings)} secrets![/red]")
        for f in findings:
            console.print(f"[red]•[/red] {f['file']}:{f['line']} ({f['secret']})")
    else:
        console.print("[green]✓ No hardcoded secrets found.[/green]")
    
    if findings and error_if_found:
        raise typer.Exit(1)


def redact_secrets(
    input_file: Optional[Path] = typer.Option(None, "--in", "-i", help="Input file"),
    output_file: Optional[Path] = typer.Option(None, "--out", "-o", help="Output file"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Specific secret only"),
    mask: str = typer.Option("***REDACTED***", "--mask", "-m", help="Mask string"),
):
    """Mask secrets in input and output."""
    secrets = []
    if name:
        data = _get_secrets(name)
        if data:
            secrets.extend([str(v) for v in data.values()])
    else:
        for n in _list_secrets():
            data = _get_secrets(n)
            if data:
                secrets.extend([str(v) for v in data.values()])
    
    secrets = sorted([s for s in secrets if len(s) >= 6], key=len, reverse=True)
    
    def redact_line(line: str) -> str:
        for secret in secrets:
            line = line.replace(secret, mask)
        return line
    
    lines = input_file.read_text().split("\n") if input_file else sys.stdin
    
    if output_file:
        with output_file.open("w") as f:
            for line in lines:
                f.write(redact_line(line.rstrip("\n")) + "\n")
    else:
        for line in lines:
            print(redact_line(line.rstrip("\n")))


def watch_and_restart(
    name: str = typer.Argument(..., help="Secret name to watch"),
    command: List[str] = typer.Argument(..., help="Command to run"),
    interval: int = typer.Option(60, "--interval", "-i", help="Check interval (seconds)"),
    on_change: str = typer.Option("restart", "--on-change", help="Action: restart, reload, exec"),
):
    """Detect secret changes and auto-restart process."""
    def get_secrets_hash():
        data = _get_secrets(name)
        if not data:
            return None
        return hashlib.sha256(str(sorted(data.items())).encode()).hexdigest()
    
    current_hash = get_secrets_hash()
    process: Optional[subprocess.Popen] = None
    
    def start_process():
        nonlocal process
        secrets = _get_secrets(name) or {}
        env = os.environ.copy()
        env.update(secrets)
        process = subprocess.Popen(command, env=env)
    
    def restart_process():
        nonlocal process
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        start_process()
    
    def signal_handler(sig, frame):
        if process is not None:
            process.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    start_process()
    console.print(f"[blue]Watching:[/blue] {name} (interval: {interval}s)")
    
    while True:
        time.sleep(interval)
        new_hash = get_secrets_hash()
        if new_hash != current_hash:
            console.print("[yellow]Secret change detected![/yellow]")
            current_hash = new_hash
            if on_change == "restart":
                restart_process()
            elif on_change == "reload" and process:
                process.send_signal(signal.SIGHUP)
        
        if process is not None and process.poll() is not None:
            console.print("[red]Process terminated, restarting...[/red]")
            start_process()
