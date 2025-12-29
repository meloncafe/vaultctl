"""vaultctl configuration management.
vaultctl 설정 관리.

Configuration priority (highest to lowest):
1. Environment variables (VAULTCTL_*, VAULT_*)
2. User config (~/.config/vaultctl/config)
3. System config (/etc/vaultctl/config) - for admin use
"""

import os
from pathlib import Path
from typing import Any, Optional, Tuple, Type

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _load_config_file(filepath: Path) -> dict[str, str]:
    """Load key=value config file / key=value 설정 파일 로드."""
    config = {}
    
    if not filepath.exists():
        return config
    
    try:
        content = filepath.read_text()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            config[key] = value
    except PermissionError:
        pass
    except Exception:
        pass
    
    return config


def _get_user_config_path() -> Path:
    """Get user config path / 사용자 설정 파일 경로."""
    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(config_home) / "vaultctl" / "config"


def _get_system_config_path() -> Path:
    """Get system config path / 시스템 설정 파일 경로."""
    return Path("/etc/vaultctl/config")


def _load_all_configs() -> dict[str, str]:
    """Load all config files with priority.
    
    Priority: user config > system config
    
    Maps VAULT_* variables to settings fields:
    - VAULT_ADDR -> vault_addr
    - VAULT_TOKEN -> vault_token
    - VAULT_ROLE_ID -> approle_role_id
    - VAULT_SECRET_ID -> approle_secret_id
    - VAULT_KV_MOUNT -> kv_mount
    - VAULT_KV_PATH -> kv_path
    """
    mapping = {
        "VAULT_ADDR": "vault_addr",
        "VAULT_TOKEN": "vault_token",
        "VAULT_NAMESPACE": "vault_namespace",
        "VAULT_ROLE_ID": "approle_role_id",
        "VAULT_SECRET_ID": "approle_secret_id",
        "VAULT_KV_MOUNT": "kv_mount",
        "VAULT_KV_PATH": "kv_path",
    }
    
    result = {}
    
    # 1. Load system config (lower priority)
    for key, value in _load_config_file(_get_system_config_path()).items():
        if key in mapping:
            result[mapping[key]] = value
    
    # 2. Load user config (higher priority, overwrites system)
    for key, value in _load_config_file(_get_user_config_path()).items():
        if key in mapping:
            result[mapping[key]] = value
    
    return result


class ConfigFileSource(PydanticBaseSettingsSource):
    """Custom settings source for config files."""
    
    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        config = _load_all_configs()
        if field_name in config:
            return config[field_name], field_name, False
        return None, field_name, False
    
    def __call__(self) -> dict[str, Any]:
        return _load_all_configs()


class Settings(BaseSettings):
    """Application settings / 애플리케이션 설정."""

    model_config = SettingsConfigDict(
        env_prefix="VAULTCTL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Vault connection
    vault_addr: str = Field(
        default="https://vault.example.com",
        description="Vault server address",
    )
    vault_token: Optional[str] = Field(
        default=None,
        description="Vault token",
    )
    vault_namespace: Optional[str] = Field(
        default=None,
        description="Vault namespace (Enterprise)",
    )
    vault_skip_verify: bool = Field(
        default=False,
        description="Skip TLS certificate verification",
    )

    # AppRole authentication
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
        description="AppRole auth mount path",
    )

    # KV path settings
    # Full path: {kv_mount}/data/{kv_path}/{name}
    # Example: kv/data/proxmox/lxc/161
    kv_mount: str = Field(
        default="kv",
        description="KV secrets engine mount path (e.g., kv)",
    )
    kv_path: str = Field(
        default="proxmox/lxc",
        description="Secret base path within KV (e.g., proxmox/lxc)",
    )

    # Token renewal settings
    token_renew_threshold: int = Field(
        default=3600,
        description="Token renewal threshold in seconds. Renew if TTL is below this.",
    )

    @property
    def config_dir(self) -> Path:
        """Config directory path (~/.config/vaultctl)."""
        config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(config_home) / "vaultctl"

    @property
    def cache_dir(self) -> Path:
        """Cache directory path (~/.cache/vaultctl)."""
        cache_home = os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        return Path(cache_home) / "vaultctl"

    @property
    def token_cache_file(self) -> Path:
        """Token cache file path."""
        return self.cache_dir / "token"

    @property
    def user_config_file(self) -> Path:
        """User config file path."""
        return self.config_dir / "config"

    def ensure_dirs(self) -> None:
        """Create necessary directories."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.chmod(0o700)

    def has_approle_credentials(self) -> bool:
        """Check if AppRole credentials are available."""
        return bool(self.approle_role_id and self.approle_secret_id)

    def get_secret_path(self, name: str) -> str:
        """Get full secret path for a name.
        
        Args:
            name: Secret name (e.g., 161, lxc-161)
            
        Returns:
            Full path (e.g., proxmox/lxc/161)
        """
        return f"{self.kv_path}/{name}"

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
        1. init_settings (constructor args)
        2. env_settings (VAULTCTL_* environment variables)
        3. dotenv_settings (.env file)
        4. config_files (~/.config/vaultctl/config, /etc/vaultctl/config)
        5. file_secret_settings (secrets directory)
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            ConfigFileSource(settings_cls),
            file_secret_settings,
        )


# Global settings instance
settings = Settings()
