from pathlib import Path

import pytest

from regulon.core.config import Settings, load_settings


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Point settings at a temp YAML file and clear REGULON_ env overrides."""
    for var in ("REGULON_APP_NAME", "REGULON_ENVIRONMENT", "REGULON_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    config_file = tmp_path / "regulon.yaml"
    monkeypatch.setenv("REGULON_CONFIG_FILE", str(config_file))
    return config_file


def test_defaults_without_yaml(isolated_env):
    settings = load_settings()
    assert settings.app_name == "regulon"
    assert settings.environment == "dev"
    assert settings.data_dir == Path("data")


def test_yaml_overrides_defaults(isolated_env):
    isolated_env.write_text("environment: ci\ndata_dir: /kb\n", encoding="utf-8")
    settings = load_settings()
    assert settings.environment == "ci"
    assert settings.data_dir == Path("/kb")


def test_env_overrides_yaml(isolated_env, monkeypatch):
    isolated_env.write_text("environment: ci\n", encoding="utf-8")
    monkeypatch.setenv("REGULON_ENVIRONMENT", "prod")
    settings = load_settings()
    assert settings.environment == "prod"


def test_config_hash_stable_and_sensitive(isolated_env, monkeypatch):
    first = load_settings().config_hash()
    assert len(first) == 12
    assert first == load_settings().config_hash()
    monkeypatch.setenv("REGULON_ENVIRONMENT", "prod")
    assert load_settings().config_hash() != first


def test_repo_config_file_loads():
    """The committed config/regulon.yaml parses into valid settings."""
    repo_root = Path(__file__).resolve().parents[2]
    assert (repo_root / "config" / "regulon.yaml").is_file()
    settings = Settings()
    assert settings.app_name == "regulon"
