"""Application settings: YAML file defaults with environment-variable overrides.

Precedence (highest wins): explicit constructor kwargs > ``QUORUM_``-prefixed environment
variables > ``config/quorum.yaml`` > field defaults. All tunables (thresholds, model names,
weights, budgets) belong in the YAML file, never as literals in code (AGENTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from quorum.core.hashing import hash_json

_CONFIG_FILE_ENV = "QUORUM_CONFIG_FILE"
_DEFAULT_CONFIG_FILE = Path("config") / "quorum.yaml"


def _config_file() -> Path:
    """Resolve the YAML config path (override with QUORUM_CONFIG_FILE)."""
    return Path(os.environ.get(_CONFIG_FILE_ENV, str(_DEFAULT_CONFIG_FILE)))


class ChunkingSettings(BaseModel):
    """Bounds for the semantic-aware chunker (characters, not tokens — model-agnostic)."""

    max_chars: int = Field(default=1200, gt=0)
    overlap_chars: int = Field(default=150, ge=0)
    min_chars: int = Field(default=200, ge=0)


class RedactionSettings(BaseModel):
    """Controls for deterministic PII redaction applied at ingest."""

    enabled: bool = True
    placeholder: str = "[REDACTED:{kind}]"
    ssn_separators: str = Field(default="-", min_length=1)
    """Separator characters accepted between the groups of an SSN-shaped number.

    Dash only by default. Filings are dense with tabulated figures, and a space-separated run like
    ``123 45 6789`` is far more likely to be three numbers in a table than a social-security
    number — redacting it would silently destroy a financial fact, which is worse for a retrieval
    corpus than leaving a placeholder unwritten. Widen this (for example to ``"-. "``) when
    ingesting document types where SSNs genuinely appear with other separators.
    """


class EdgarSettings(BaseModel):
    """Client settings for the public SEC EDGAR API.

    EDGAR asks callers to identify themselves with a descriptive User-Agent. The default is
    generic on purpose (the repo carries no personal contact details); override it with
    ``QUORUM_INGESTION__EDGAR__USER_AGENT`` before fetching real filings.
    """

    user_agent: str = "quorum-open-source-learning-project (contact via GitHub issues)"
    base_url: str = "https://www.sec.gov"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    min_request_interval_seconds: float = Field(default=0.2, ge=0)


class IngestionSettings(BaseModel):
    """Everything the ingestion pipeline can be tuned with."""

    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    redaction: RedactionSettings = Field(default_factory=RedactionSettings)
    edgar: EdgarSettings = Field(default_factory=EdgarSettings)


class Settings(BaseSettings):
    """Top-level Quorum settings."""

    model_config = SettingsConfigDict(env_prefix="QUORUM_", env_nested_delimiter="__", extra="ignore")

    app_name: str = "quorum"
    environment: str = "dev"
    data_dir: Path = Path("data")
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)

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
