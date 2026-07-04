"""Tests for GrACE shared config."""

from src.shared.config import GraceSettings, get_settings


def test_settings_loads_from_env():
    """GraceSettings loads DATABASE_URL from .env."""
    settings = get_settings()
    assert settings.database_url
    assert "postgresql" in settings.database_url


def test_settings_has_required_fields():
    """All expected fields exist with correct types."""
    settings = get_settings()
    assert isinstance(settings.database_url, str)
    assert isinstance(settings.ollama_base_url, str)
    assert isinstance(settings.ollama_model, str)
    assert isinstance(settings.ollama_embed_model, str)
    assert isinstance(settings.grace_host, str)
    assert isinstance(settings.grace_port, int)
    assert isinstance(settings.discovery_source_dir, str)
