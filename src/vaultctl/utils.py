"""유틸리티 함수."""

import os
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Optional

from rich.console import Console
from rich.table import Table

console = Console()


def copy_to_clipboard(text: str) -> bool:
    """텍스트를 클립보드에 복사."""
    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            subprocess.run(
                ["pbcopy"],
                input=text.encode(),
                check=True,
                timeout=5,
            )
            return True
        elif system == "Linux":
            # xclip 시도
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
            # xsel 시도
            try:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
            # wl-copy 시도 (Wayland)
            try:
                subprocess.run(
                    ["wl-copy"],
                    input=text.encode(),
                    check=True,
                    timeout=5,
                )
                return True
            except FileNotFoundError:
                pass
        elif system == "Windows":
            subprocess.run(
                ["clip"],
                input=text.encode(),
                check=True,
                timeout=5,
                shell=True,
            )
            return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    return False


def format_duration(seconds: int) -> str:
    """초를 사람이 읽기 좋은 형식으로 변환."""
    if seconds <= 0:
        return "만료됨"

    delta = timedelta(seconds=seconds)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    if secs > 0 and not parts:
        parts.append(f"{secs}초")

    return " ".join(parts) if parts else "곧 만료"


def format_timestamp(ts: Optional[str]) -> str:
    """ISO 타임스탬프를 로컬 시간으로 변환."""
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts


def create_kv_table(data: dict, title: str = "Secrets") -> Table:
    """KV 데이터를 Rich 테이블로 변환."""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Key", style="green")
    table.add_column("Value", style="white")

    for key, value in sorted(data.items()):
        # 비밀번호 등 민감한 필드는 마스킹
        display_value = str(value)
        if any(
            sensitive in key.lower()
            for sensitive in ["password", "secret", "token", "key", "credential"]
        ):
            if len(display_value) > 4:
                display_value = display_value[:2] + "*" * (len(display_value) - 4) + display_value[-2:]
            else:
                display_value = "*" * len(display_value)

        table.add_row(key, display_value)

    return table


def parse_key_value_args(args: list[str]) -> dict[str, str]:
    """key=value 형식의 인자를 딕셔너리로 변환."""
    result = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def load_env_file(path: str) -> dict[str, str]:
    """환경변수 파일 로드."""
    result = {}
    if not os.path.exists(path):
        return result

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            # 주석, 빈 줄 무시
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                # 따옴표 제거
                value = value.strip().strip("'\"")
                result[key.strip()] = value

    return result


def write_env_file(path: str, data: dict[str, str], header: Optional[str] = None) -> None:
    """환경변수 파일 저장."""
    with open(path, "w") as f:
        if header:
            f.write(f"# {header}\n")
        f.write(f"# Generated at: {datetime.now().isoformat()}\n\n")
        for key, value in sorted(data.items()):
            # 특수문자가 있으면 따옴표로 감싸기
            if any(c in value for c in [" ", "'", '"', "$", "\n"]):
                value = f'"{value}"'
            f.write(f"{key}={value}\n")
