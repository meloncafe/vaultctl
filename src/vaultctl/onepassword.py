"""1Password CLI 연동."""

import json
import subprocess
from typing import Optional

from rich.console import Console

from vaultctl.config import settings

console = Console()


class OnePasswordError(Exception):
    """1Password 관련 오류."""

    pass


def is_op_installed() -> bool:
    """1Password CLI 설치 여부 확인."""
    try:
        result = subprocess.run(
            ["op", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_op_signed_in() -> bool:
    """1Password 로그인 상태 확인."""
    try:
        result = subprocess.run(
            ["op", "account", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        accounts = json.loads(result.stdout) if result.stdout else []
        return len(accounts) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False


def op_signin() -> bool:
    """1Password 로그인 (인터랙티브)."""
    try:
        # eval $(op signin) 대신 직접 호출
        result = subprocess.run(
            ["op", "signin", "--raw"],
            capture_output=False,  # 사용자 입력 허용
            timeout=60,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def op_read(reference: str) -> Optional[str]:
    """1Password에서 값 읽기.

    Args:
        reference: 1Password 참조 (예: "op://Vault/Item/Field")

    Returns:
        읽은 값 또는 None
    """
    try:
        result = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def op_item_get(vault: str, item: str, field: str) -> Optional[str]:
    """1Password 아이템에서 특정 필드 값 읽기."""
    reference = f"op://{vault}/{item}/{field}"
    return op_read(reference)


def op_item_create(
    vault: str,
    title: str,
    fields: dict[str, str],
    category: str = "login",
) -> bool:
    """1Password 아이템 생성."""
    try:
        cmd = [
            "op",
            "item",
            "create",
            f"--category={category}",
            f"--title={title}",
            f"--vault={vault}",
        ]
        for key, value in fields.items():
            cmd.append(f"{key}={value}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def op_item_edit(vault: str, item: str, fields: dict[str, str]) -> bool:
    """1Password 아이템 수정."""
    try:
        cmd = ["op", "item", "edit", item, f"--vault={vault}"]
        for key, value in fields.items():
            cmd.append(f"{key}={value}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_vault_token_from_op() -> Optional[str]:
    """1Password에서 Vault 토큰 읽기."""
    return op_item_get(
        vault=settings.op_vault,
        item=settings.op_item,
        field=settings.op_field,
    )


def save_vault_token_to_op(token: str) -> bool:
    """Vault 토큰을 1Password에 저장."""
    # 기존 아이템 확인
    existing = op_item_get(
        vault=settings.op_vault,
        item=settings.op_item,
        field=settings.op_field,
    )

    fields = {
        settings.op_field: token,
        "url": settings.vault_addr,
    }

    if existing:
        return op_item_edit(
            vault=settings.op_vault,
            item=settings.op_item,
            fields=fields,
        )
    else:
        return op_item_create(
            vault=settings.op_vault,
            title=settings.op_item,
            fields=fields,
        )
