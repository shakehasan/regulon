"""Application settings: YAML file defaults with environment-variable overrides.

Precedence (highest wins): explicit constructor kwargs > ``REGULON_``-prefixed environment
variables > ``config/regulon.yaml`` > field defaults. All tunables (thresholds, model names,
weights, budgets) belong in the YAML file, never as literals in code (AGENTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from regulon.core.hashing import hash_json

_CONFIG_FILE_ENV = "REGULON_CONFIG_FILE"
_DEFAULT_CONFIG_FILE = Path("config") / "regulon.yaml"


def _config_file() -> Path:
    """Resolve the YAML config path (override with REGULON_CONFIG_FILE)."""
    return Path(os.environ.get(_CONFIG_FILE_ENV, str(_DEFAULT_CONFIG_FILE)))


class Settings(BaseSettings):
    """Top-level Regulon settings."""

    model_config = SettingsConfigDict(env_prefix="REGULON_", extra="ignore")

    app_name: str = "regulon"
    environment: str = "dev"
    data_dir: Path = Path("data")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the YAML file below env vars in the precedence order."""
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=_config_file())
        return (init_settings, env_settings, dotenv_settings, yaml_source, file_secret_settings)

    def config_hash(self) -> str:
        """Return a short stable hash of the resolved configuration.

        Reports and eval artifacts embed this hash so every number is traceable to the
        exact configuration that produced it.
        """
        return hash_json(self.model_dump(mode="json"))[:12]


def load_settings() -> Settings:
    """Load settings from YAML + environment."""
    return Settings()
