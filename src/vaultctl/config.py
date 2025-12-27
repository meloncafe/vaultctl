"""Vault CLI 설정 관리."""

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 설정."""

    model_config = SettingsConfigDict(
        env_prefix="VAULTCTL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Vault 설정
    vault_addr: str = Field(
        default="https://vault.example.com",
        description="Vault 서버 주소",
    )
    vault_token: Optional[str] = Field(
        default=None,
        description="Vault 토큰 (환경변수 또는 1Password에서 로드)",
    )
    vault_namespace: Optional[str] = Field(
        default=None,
        description="Vault 네임스페이스 (Enterprise)",
    )
    vault_skip_verify: bool = Field(
        default=False,
        description="TLS 인증서 검증 스킵",
    )

    # 1Password 설정
    op_vault: str = Field(
        default="Infrastructure",
        description="1Password Vault 이름",
    )
    op_item: str = Field(
        default="vault-token",
        description="1Password Item 이름",
    )
    op_field: str = Field(
        default="credential",
        description="1Password 필드 이름",
    )

    # KV 경로 설정
    kv_mount: str = Field(
        default="proxmox",
        description="KV secrets engine 마운트 경로",
    )
    kv_lxc_path: str = Field(
        default="lxc",
        description="LXC 정보 저장 경로",
    )
    kv_docker_path: str = Field(
        default="docker",
        description="Docker 환경변수 저장 경로",
    )
    kv_urls_path: str = Field(
        default="urls",
        description="URL 저장 경로",
    )

    # 토큰 갱신 설정
    token_renew_threshold: int = Field(
        default=3600,
        description="토큰 갱신 임계값 (초). TTL이 이 값 이하면 갱신",
    )
    token_renew_increment: int = Field(
        default=86400,
        description="토큰 갱신 시 증가량 (초)",
    )

    @property
    def config_dir(self) -> Path:
        """설정 디렉토리 경로."""
        config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(config_home) / "vaultctl"

    @property
    def cache_dir(self) -> Path:
        """캐시 디렉토리 경로."""
        cache_home = os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        return Path(cache_home) / "vaultctl"

    @property
    def token_cache_file(self) -> Path:
        """토큰 캐시 파일 경로."""
        return self.cache_dir / "token"

    def ensure_dirs(self) -> None:
        """필요한 디렉토리 생성."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 캐시 디렉토리 권한 설정
        self.cache_dir.chmod(0o700)


# 전역 설정 인스턴스
settings = Settings()
