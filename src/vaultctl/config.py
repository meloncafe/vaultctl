"""Vault CLI 설정 관리."""

import os
from pathlib import Path
from typing import Any, Optional, Tuple, Type

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


# System config file path / 시스템 설정 파일 경로
SYSTEM_CONFIG_FILE = Path("/etc/vaultctl/config")


def _load_system_config() -> dict[str, str]:
    """Load /etc/vaultctl/config file / /etc/vaultctl/config 파일 로드.
    
    Maps VAULT_* variables to settings fields:
    - VAULT_ADDR -> vault_addr
    - VAULT_TOKEN -> vault_token
    - VAULT_ROLE_ID -> approle_role_id
    - VAULT_SECRET_ID -> approle_secret_id
    """
    config = {}
    
    if not SYSTEM_CONFIG_FILE.exists():
        return config
    
    try:
        content = SYSTEM_CONFIG_FILE.read_text()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            
            # Map VAULT_* to settings field names
            mapping = {
                "VAULT_ADDR": "vault_addr",
                "VAULT_TOKEN": "vault_token",
                "VAULT_NAMESPACE": "vault_namespace",
                "VAULT_ROLE_ID": "approle_role_id",
                "VAULT_SECRET_ID": "approle_secret_id",
            }
            
            if key in mapping:
                config[mapping[key]] = value
    except Exception:
        pass
    
    return config


class SystemConfigSource(PydanticBaseSettingsSource):
    """Custom settings source for /etc/vaultctl/config."""
    
    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        config = _load_system_config()
        if field_name in config:
            return config[field_name], field_name, False
        return None, field_name, False
    
    def __call__(self) -> dict[str, Any]:
        return _load_system_config()


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
        description="Vault 토큰",
    )
    vault_namespace: Optional[str] = Field(
        default=None,
        description="Vault 네임스페이스 (Enterprise)",
    )
    vault_skip_verify: bool = Field(
        default=False,
        description="TLS 인증서 검증 스킵",
    )

    # AppRole 설정
    approle_role_id: Optional[str] = Field(
        default=None,
        description="AppRole Role ID",
    )
    approle_secret_id: Optional[str] = Field(
        default=None,
        description="AppRole Secret ID",
    )
    approle_mount: str = Field(
        default="approle",
        description="AppRole 인증 마운트 경로",
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

    def has_approle_credentials(self) -> bool:
        """AppRole 자격 증명이 있는지 확인."""
        return bool(self.approle_role_id and self.approle_secret_id)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources.
        
        Priority (highest to lowest):
        1. init_settings (passed to constructor)
        2. env_settings (environment variables)
        3. dotenv_settings (.env file)
        4. system_config (/etc/vaultctl/config)
        5. file_secret_settings (secrets directory)
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            SystemConfigSource(settings_cls),
            file_secret_settings,
        )


# 전역 설정 인스턴스
settings = Settings()
