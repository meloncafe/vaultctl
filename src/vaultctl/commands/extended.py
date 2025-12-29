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


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════


def _get_authenticated_client() -> VaultClient:
    """Get authenticated Vault client / 인증된 클라이언트 반환."""
    client = VaultClient()
    
    # Try cached token
    if settings.token_cache_file.exists():
        try:
            token = settings.token_cache_file.read_text().strip()
            if token:
                client = VaultClient(token=token)
                if client.is_authenticated():
                    return client
        except PermissionError:
            pass
    
    # Try config token
    if settings.vault_token:
        client = VaultClient(token=settings.vault_token)
        if client.is_authenticated():
            return client
    
    # Try AppRole
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
    """Get secrets / 시크릿 조회."""
    client = _get_authenticated_client()
    secret_path = settings.get_secret_path(name)
    try:
        return client.kv_get(settings.kv_mount, secret_path)
    except VaultError:
        return {}


def _list_secrets() -> list[str]:
    """List secrets / 시크릿 목록."""
    client = _get_authenticated_client()
    try:
        keys = client.kv_list(settings.kv_mount, settings.kv_path)
        return [k.rstrip("/") for k in keys]
    except VaultError:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl run - Run with injected env vars
# ═══════════════════════════════════════════════════════════════════════════════

def run_command(
    name: str = typer.Argument(..., help="Secret name (e.g., 100) / 시크릿 이름"),
    command: List[str] = typer.Argument(..., help="Command to run / 실행할 명령어"),
    reset: bool = typer.Option(False, "--reset", "-r", help="Reset existing env vars / 기존 환경변수 초기화"),
    shell: bool = typer.Option(False, "--shell", "-s", help="Run through shell / 셸을 통해 실행"),
):
    """Run process with injected environment variables.
    환경변수를 주입하면서 프로세스 실행.
    
    \b
    Examples:
        vaultctl run 100 -- node index.js
        vaultctl run 100 --shell -- "echo $DB_PASSWORD"
        vaultctl run 100 -- docker compose up -d
    """
    secrets = _get_secrets(name)
    
    if not secrets:
        console.print(f"[red]✗[/red] Secret not found: {name}")
        raise typer.Exit(1)
    
    # Build environment
    if reset:
        env = dict(secrets)
        # Keep essential env vars
        for key in ["PATH", "HOME", "USER", "SHELL", "TERM"]:
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        env = os.environ.copy()
        env.update(secrets)
    
    console.print(f"[green]▶[/green] Loaded {len(secrets)} environment variables")
    
    # Run command
    if shell:
        cmd = " ".join(command)
        result = subprocess.run(cmd, shell=True, env=env)
    else:
        result = subprocess.run(command, env=env)
    
    raise typer.Exit(result.returncode)


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl sh - Shell integration
# ═══════════════════════════════════════════════════════════════════════════════

def shell_export(
    name: str = typer.Argument(..., help="Secret name (e.g., 100) / 시크릿 이름"),
    _format: str = typer.Option("bash", "--format", "-f", help="Output format: bash, fish, zsh / 출력 형식"),
):
    """Generate shell export statements for eval.
    셸에서 eval로 사용할 export 문 생성.
    
    \b
    Examples:
        eval "$(vaultctl sh 100)"
        
    Add to .bashrc/.zshrc:
        eval "$(vaultctl sh 100)"
    """
    secrets = _get_secrets(name)
    
    if not secrets:
        return
    
    for key, value in secrets.items():
        # Escape value
        escaped = str(value).replace("'", "'\"'\"'")
        
        if _format == "fish":
            print(f"set -gx {key} '{escaped}'")
        else:
            print(f"export {key}='{escaped}'")


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl scan - Secret scanning (DevSecOps)
# ═══════════════════════════════════════════════════════════════════════════════

def scan_secrets(
    path: Path = typer.Argument(".", help="Path to scan / 스캔할 경로"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Specific secret only / 특정 시크릿만 검색"),
    error_if_found: bool = typer.Option(False, "--error-if-found", help="Exit with error if found (for CI) / 발견 시 에러 코드 반환"),
    json_output: bool = typer.Option(False, "--json", help="JSON output / JSON 형식 출력"),
    exclude: List[str] = typer.Option(
        [".git", "node_modules", "__pycache__", ".venv", "venv", ".env"],
        "--exclude", "-e",
        help="Directories/files to exclude / 제외할 디렉토리/파일"
    ),
):
    """Scan code for hardcoded secrets from Vault.
    코드에서 Vault에 저장된 비밀이 하드코딩되어 있는지 검색.
    
    \b
    Examples:
        vaultctl scan
        vaultctl scan ./src --name 100
        vaultctl scan --error-if-found  # For CI/CD
    """
    # Collect secrets
    secrets_to_find = {}
    
    if name:
        data = _get_secrets(name)
        if data:
            for key, value in data.items():
                if len(str(value)) >= 8:  # Exclude short values
                    secrets_to_find[f"{name}/{key}"] = str(value)
    else:
        # All secrets
        names = _list_secrets()
        for n in names:
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
    
    # Scan files
    for file_path in path.rglob("*"):
        # Check excluded directories
        if any(ex in str(file_path) for ex in exclude):
            continue
        
        if not file_path.is_file():
            continue
        
        # Skip binary files
        try:
            content = file_path.read_text(errors="ignore")
        except Exception:
            continue
        
        for secret_id, secret_value in secrets_to_find.items():
            if secret_value in content:
                # Find line number
                for i, line in enumerate(content.split("\n"), 1):
                    if secret_value in line:
                        findings.append({
                            "file": str(file_path),
                            "line": i,
                            "secret": secret_id,
                            "preview": line[:80] + "..." if len(line) > 80 else line
                        })
    
    # Output results
    if json_output:
        print(json.dumps(findings, indent=2))
    else:
        if findings:
            console.print(f"\n[red]⚠ Found {len(findings)} secrets![/red]\n")
            for f in findings:
                console.print(f"[red]•[/red] {f['file']}:{f['line']}")
                console.print(f"  [dim]Secret: {f['secret']}[/dim]")
                console.print()
        else:
            console.print("[green]✓ No hardcoded secrets found.[/green]")
    
    if findings and error_if_found:
        raise typer.Exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl redact - Log redaction
# ═══════════════════════════════════════════════════════════════════════════════

def redact_secrets(
    input_file: Optional[Path] = typer.Option(None, "--in", "-i", help="Input file (stdin if omitted) / 입력 파일"),
    output_file: Optional[Path] = typer.Option(None, "--out", "-o", help="Output file (stdout if omitted) / 출력 파일"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Specific secret only / 특정 시크릿만"),
    mask: str = typer.Option("***REDACTED***", "--mask", "-m", help="Mask string / 마스킹 문자열"),
):
    """Mask secrets in input and output.
    입력에서 비밀을 마스킹하여 출력.
    
    \b
    Examples:
        cat app.log | vaultctl redact
        tail -f /var/log/app.log | vaultctl redact
        vaultctl redact --in dirty.log --out clean.log
    """
    # Collect secrets
    secrets = []
    
    if name:
        data = _get_secrets(name)
        if data:
            secrets.extend([str(v) for v in data.values()])
    else:
        names = _list_secrets()
        for n in names:
            data = _get_secrets(n)
            if data:
                secrets.extend([str(v) for v in data.values()])
    
    # Exclude short values, sort by length (longest first)
    secrets = sorted(
        [s for s in secrets if len(s) >= 6],
        key=len,
        reverse=True
    )
    
    def redact_line(line: str) -> str:
        for secret in secrets:
            line = line.replace(secret, mask)
        return line
    
    # Process input
    if input_file:
        content = input_file.read_text()
        lines = content.split("\n")
    else:
        lines = sys.stdin
    
    # Process output
    if output_file:
        with output_file.open("w") as f:
            for line in lines:
                f.write(redact_line(line.rstrip("\n")) + "\n")
    else:
        for line in lines:
            print(redact_line(line.rstrip("\n")))


# ═══════════════════════════════════════════════════════════════════════════════
# vaultctl watch - Secret change detection
# ═══════════════════════════════════════════════════════════════════════════════

def watch_and_restart(
    name: str = typer.Argument(..., help="Secret name to watch / 감시할 시크릿 이름"),
    command: List[str] = typer.Argument(..., help="Command to run / 실행할 명령어"),
    interval: int = typer.Option(60, "--interval", "-i", help="Check interval (seconds) / 체크 간격 (초)"),
    on_change: str = typer.Option("restart", "--on-change", help="Action on change: restart, reload, exec / 변경 시 동작"),
):
    """Detect secret changes and auto-restart process.
    비밀 변경을 감지하고 프로세스 자동 재시작.
    
    \b
    Examples:
        vaultctl watch 100 -- docker compose up -d
        vaultctl watch 100 --interval 300 -- systemctl restart myapp
    
    Register as systemd service:
        [Service]
        ExecStart=/usr/bin/vaultctl watch 100 -- docker compose up
        Restart=always
    """
    def get_secrets_hash():
        data = _get_secrets(name)
        if not data:
            return None
        content = str(sorted(data.items()))
        return hashlib.sha256(content.encode()).hexdigest()
    
    current_hash = get_secrets_hash()
    process: Optional[subprocess.Popen] = None
    
    def start_process():
        nonlocal process
        console.print(f"[green]▶[/green] Starting process: {' '.join(command)}")
        
        # Load env vars
        secrets = _get_secrets(name) or {}
        env = os.environ.copy()
        env.update(secrets)
        
        process = subprocess.Popen(command, env=env)
    
    def restart_process():
        nonlocal process
        _proc = process
        if _proc is not None:
            console.print("[yellow]⟳[/yellow] Restarting process...")
            _proc.terminate()
            try:
                _proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _proc.kill()
        start_process()
    
    def signal_handler(sig, frame):
        nonlocal process
        console.print("\n[red]Interrupted[/red]")
        _proc = process
        if _proc is not None:
            _proc.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initial start
    start_process()
    
    console.print(f"[blue]👁[/blue] Watching: {name} (interval: {interval}s)")
    
    while True:
        time.sleep(interval)
        
        new_hash = get_secrets_hash()
        if new_hash != current_hash:
            console.print(f"[yellow]⚡[/yellow] Secret change detected!")
            current_hash = new_hash
            
            if on_change == "restart":
                restart_process()
            elif on_change == "reload":
                proc = process
                if proc is not None:
                    proc.send_signal(signal.SIGHUP)
            elif on_change == "exec":
                subprocess.run(command)
        
        # Check process status
        proc = process
        if proc is not None and proc.poll() is not None:
            console.print("[red]Process terminated, restarting...[/red]")
            start_process()
