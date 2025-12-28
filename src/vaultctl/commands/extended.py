"""
vaultctl 확장 기능 (teller 영감)

추가 명령어:
- vaultctl run: 환경변수 주입하며 프로세스 실행
- vaultctl scan: 코드에서 비밀 검색
- vaultctl redact: 로그에서 비밀 마스킹
- vaultctl sh: 셸 통합용 export 생성
- vaultctl watch: 비밀 변경 감지 및 자동 재시작
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
from vaultctl.vault_client import get_client, VaultError

app = typer.Typer(help="확장 명령어 (teller 스타일)")
console = Console()


def _get_docker_secrets(service: str) -> dict:
    """Docker 서비스의 환경변수 조회."""
    client = get_client()
    try:
        return client.kv_get(settings.kv_mount, f"{settings.kv_docker_path}/{service}")
    except VaultError:
        return {}


def _get_lxc_secrets(name: str) -> dict:
    """LXC의 시크릿 조회."""
    client = get_client()
    try:
        return client.kv_get(settings.kv_mount, f"{settings.kv_lxc_path}/{name}")
    except VaultError:
        return {}


def _list_docker_services() -> list[str]:
    """Docker 서비스 목록."""
    client = get_client()
    try:
        keys = client.kv_list(settings.kv_mount, settings.kv_docker_path)
        return [k.rstrip("/") for k in keys]
    except VaultError:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# vaultctl run - 환경변수 주입하며 프로세스 실행
# ─────────────────────────────────────────────────────────────────────────────

def run_command(
    service: str = typer.Argument(..., help="Docker 서비스명 또는 LXC 이름"),
    command: List[str] = typer.Argument(..., help="실행할 명령어"),
    reset: bool = typer.Option(False, "--reset", "-r", help="기존 환경변수 초기화"),
    shell: bool = typer.Option(False, "--shell", "-s", help="셸을 통해 실행"),
    source: str = typer.Option("docker", "--source", help="소스: docker, lxc"),
):
    """
    환경변수를 주입하면서 프로세스 실행.
    
    예시:
        vaultctl run n8n -- node index.js
        vaultctl run n8n --shell -- "echo $DB_PASSWORD"
        vaultctl run 130-n8n --source lxc -- ./script.sh
    """
    # 환경변수 가져오기
    if source == "docker":
        secrets = _get_docker_secrets(service)
    else:
        secrets = _get_lxc_secrets(service)
    
    if not secrets:
        console.print(f"[red]✗[/red] '{service}'에서 시크릿을 찾을 수 없습니다.")
        raise typer.Exit(1)
    
    # 환경 구성
    if reset:
        env = dict(secrets)
        # 필수 환경변수는 유지
        for key in ["PATH", "HOME", "USER", "SHELL", "TERM"]:
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        env = os.environ.copy()
        env.update(secrets)
    
    console.print(f"[green]▶[/green] {len(secrets)}개 환경변수 로드됨")
    
    # 명령어 실행
    if shell:
        cmd = " ".join(command)
        result = subprocess.run(cmd, shell=True, env=env)
    else:
        result = subprocess.run(command, env=env)
    
    raise typer.Exit(result.returncode)


# ─────────────────────────────────────────────────────────────────────────────
# vaultctl sh - 셸 통합용 export 생성
# ─────────────────────────────────────────────────────────────────────────────

def shell_export(
    service: str = typer.Argument(..., help="Docker 서비스명"),
    _format: str = typer.Option("bash", "--format", "-f", help="출력 형식: bash, fish, zsh"),
):
    """
    셸에서 eval로 사용할 export 문 생성.
    
    예시:
        eval "$(vaultctl sh n8n)"
        
    .zshrc에 추가:
        eval "$(vaultctl sh n8n)"
    """
    secrets = _get_docker_secrets(service)
    
    if not secrets:
        return
    
    for key, value in secrets.items():
        # 값 이스케이프
        escaped = str(value).replace("'", "'\"'\"'")
        
        if _format == "fish":
            print(f"set -gx {key} '{escaped}'")
        else:
            print(f"export {key}='{escaped}'")


# ─────────────────────────────────────────────────────────────────────────────
# vaultctl scan - 코드에서 비밀 검색
# ─────────────────────────────────────────────────────────────────────────────

def scan_secrets(
    path: Path = typer.Argument(".", help="스캔할 경로"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="특정 서비스의 비밀만 검색"),
    error_if_found: bool = typer.Option(False, "--error-if-found", help="발견 시 에러 코드 반환 (CI용)"),
    json_output: bool = typer.Option(False, "--json", help="JSON 형식 출력"),
    exclude: List[str] = typer.Option(
        [".git", "node_modules", "__pycache__", ".venv", "venv", ".env"],
        "--exclude", "-e",
        help="제외할 디렉토리/파일"
    ),
):
    """
    코드에서 Vault에 저장된 비밀이 하드코딩되어 있는지 검색.
    
    예시:
        vaultctl scan
        vaultctl scan ./src --service n8n
        vaultctl scan --error-if-found  # CI용
    """
    # 비밀 수집
    secrets_to_find = {}
    
    if service:
        data = _get_docker_secrets(service)
        if data:
            for key, value in data.items():
                if len(str(value)) >= 8:  # 짧은 값은 제외
                    secrets_to_find[f"{service}/{key}"] = str(value)
    else:
        # 모든 Docker 서비스
        services = _list_docker_services()
        for svc in services:
            data = _get_docker_secrets(svc)
            if data:
                for key, value in data.items():
                    if len(str(value)) >= 8:
                        secrets_to_find[f"{svc}/{key}"] = str(value)
    
    if not secrets_to_find:
        console.print("[yellow]스캔할 비밀이 없습니다.[/yellow]")
        return
    
    console.print(f"[blue]스캔 중...[/blue] {len(secrets_to_find)}개 비밀, 경로: {path}")
    
    findings = []
    
    # 파일 스캔
    for file_path in path.rglob("*"):
        # 제외 디렉토리 확인
        if any(ex in str(file_path) for ex in exclude):
            continue
        
        if not file_path.is_file():
            continue
        
        # 바이너리 파일 제외
        try:
            content = file_path.read_text(errors="ignore")
        except Exception:
            continue
        
        for secret_id, secret_value in secrets_to_find.items():
            if secret_value in content:
                # 라인 번호 찾기
                for i, line in enumerate(content.split("\n"), 1):
                    if secret_value in line:
                        findings.append({
                            "file": str(file_path),
                            "line": i,
                            "secret": secret_id,
                            "preview": line[:80] + "..." if len(line) > 80 else line
                        })
    
    # 결과 출력
    if json_output:
        print(json.dumps(findings, indent=2))
    else:
        if findings:
            console.print(f"\n[red]⚠ {len(findings)}개 비밀 발견![/red]\n")
            for f in findings:
                console.print(f"[red]•[/red] {f['file']}:{f['line']}")
                console.print(f"  [dim]Secret: {f['secret']}[/dim]")
                console.print()
        else:
            console.print("[green]✓ 비밀이 발견되지 않았습니다.[/green]")
    
    if findings and error_if_found:
        raise typer.Exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# vaultctl redact - 로그에서 비밀 마스킹
# ─────────────────────────────────────────────────────────────────────────────

def redact_secrets(
    input_file: Optional[Path] = typer.Option(None, "--in", "-i", help="입력 파일 (없으면 stdin)"),
    output_file: Optional[Path] = typer.Option(None, "--out", "-o", help="출력 파일 (없으면 stdout)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="특정 서비스의 비밀만"),
    mask: str = typer.Option("***REDACTED***", "--mask", "-m", help="마스킹 문자열"),
):
    """
    입력에서 비밀을 마스킹하여 출력.
    
    예시:
        cat app.log | vaultctl redact
        tail -f /var/log/app.log | vaultctl redact
        vaultctl redact --in dirty.log --out clean.log
    """
    # 비밀 수집
    secrets = []
    
    if service:
        data = _get_docker_secrets(service)
        if data:
            secrets.extend([str(v) for v in data.values()])
    else:
        services = _list_docker_services()
        for svc in services:
            data = _get_docker_secrets(svc)
            if data:
                secrets.extend([str(v) for v in data.values()])
    
    # 짧은 값 제외, 길이순 정렬 (긴 것부터 교체)
    secrets = sorted(
        [s for s in secrets if len(s) >= 6],
        key=len,
        reverse=True
    )
    
    def redact_line(line: str) -> str:
        for secret in secrets:
            line = line.replace(secret, mask)
        return line
    
    # 입력 처리
    if input_file:
        content = input_file.read_text()
        lines = content.split("\n")
    else:
        lines = sys.stdin
    
    # 출력 처리
    if output_file:
        with output_file.open("w") as f:
            for line in lines:
                f.write(redact_line(line.rstrip("\n")) + "\n")
    else:
        for line in lines:
            print(redact_line(line.rstrip("\n")))


# ─────────────────────────────────────────────────────────────────────────────
# vaultctl watch - 비밀 변경 감지 및 자동 재시작
# ─────────────────────────────────────────────────────────────────────────────

def watch_and_restart(
    service: str = typer.Argument(..., help="감시할 서비스"),
    command: List[str] = typer.Argument(..., help="실행할 명령어"),
    interval: int = typer.Option(60, "--interval", "-i", help="체크 간격 (초)"),
    on_change: str = typer.Option("restart", "--on-change", help="변경 시 동작: restart, reload, exec"),
):
    """
    비밀 변경을 감지하고 프로세스 자동 재시작.
    
    예시:
        vaultctl watch n8n -- docker-compose up -d
        vaultctl watch n8n --interval 300 -- systemctl restart n8n
    
    systemd 서비스로 등록하여 사용:
        [Service]
        ExecStart=/usr/bin/vaultctl watch n8n -- docker-compose -f /opt/n8n/docker-compose.yml up -d
        Restart=always
    """
    def get_secrets_hash():
        data = _get_docker_secrets(service)
        if not data:
            return None
        content = str(sorted(data.items()))
        return hashlib.sha256(content.encode()).hexdigest()
    
    current_hash = get_secrets_hash()
    process: Optional[subprocess.Popen] = None
    
    def start_process():
        nonlocal process
        console.print(f"[green]▶[/green] 프로세스 시작: {' '.join(command)}")
        
        # 환경변수 로드
        secrets = _get_docker_secrets(service) or {}
        env = os.environ.copy()
        env.update(secrets)
        
        process = subprocess.Popen(command, env=env)
    
    def restart_process():
        nonlocal process
        _proc = process  # 로컬 변수에 캡처하여 타입 좁히기
        if _proc is not None:
            console.print("[yellow]⟳[/yellow] 프로세스 재시작 중...")
            _proc.terminate()
            try:
                _proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _proc.kill()
        start_process()
    
    def signal_handler(sig, frame):
        nonlocal process
        console.print("\n[red]중단됨[/red]")
        _proc = process
        if _proc is not None:
            _proc.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 초기 실행
    start_process()
    
    console.print(f"[blue]👁[/blue] 감시 중: {service} (간격: {interval}초)")
    
    while True:
        time.sleep(interval)
        
        new_hash = get_secrets_hash()
        if new_hash != current_hash:
            console.print(f"[yellow]⚡[/yellow] 비밀 변경 감지!")
            current_hash = new_hash
            
            if on_change == "restart":
                restart_process()
            elif on_change == "reload":
                proc = process
                if proc is not None:
                    proc.send_signal(signal.SIGHUP)
            elif on_change == "exec":
                subprocess.run(command)
        
        # 프로세스 상태 확인
        proc = process
        if proc is not None and proc.poll() is not None:
            console.print("[red]프로세스 종료됨, 재시작...[/red]")
            start_process()
