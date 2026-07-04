"""Tests for LLM provider factory, registry, and config."""

from unittest.mock import patch

import pytest

from src.shared.llm_provider import (
    PROVIDER_REGISTRY,
    _mask_api_key,
    get_provider,
    get_provider_display_config,
    read_llm_config_from_yaml,
)


def test_get_provider_ollama_default():
    """No special config -> returns OllamaProvider."""
    provider = get_provider(config_override={
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "timeout": 300,
        "api_key": "",
    })
    assert provider.provider_name == "ollama"


def test_get_provider_anthropic():
    """provider=anthropic + api_key -> AnthropicProvider."""
    provider = get_provider(config_override={
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20250414",
        "api_key": "sk-ant-test-key-12345678",
        "timeout": 300,
    })
    assert provider.provider_name == "anthropic"


def test_get_provider_openai():
    """provider=openai + api_key -> OpenAICompatibleProvider."""
    provider = get_provider(config_override={
        "provider": "openai",
        "model": "gpt-4.1-nano",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-key-12345678",
        "timeout": 300,
    })
    assert provider.provider_name == "openai"


def test_get_provider_invalid():
    """provider=fakeprovider -> raises ValueError."""
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider(config_override={
            "provider": "fakeprovider",
            "model": "x",
            "api_key": "",
        })


def test_get_provider_cloud_no_key():
    """provider=anthropic, no API key -> raises ValueError."""
    with pytest.raises(ValueError, match="requires LLM_API_KEY"):
        get_provider(config_override={
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20250414",
            "api_key": "",
        })


def test_get_provider_config_override():
    """config_override dict used instead of yaml."""
    provider = get_provider(config_override={
        "provider": "ollama",
        "model": "llama3:8b",
        "base_url": "http://custom:11434",
        "timeout": 60,
        "api_key": "",
    })
    assert provider.provider_name == "ollama"
    assert provider.config.model == "llama3:8b"
    assert provider.config.base_url == "http://custom:11434"


def test_provider_display_masks_key():
    """API key shown as first 4 chars + '...' (Observation 3 ratification)."""
    assert _mask_api_key("sk-ant-api03-abcdefghijklmnop") == "sk-a..."
    assert _mask_api_key("abcd") == "abcd..."
    assert _mask_api_key("abcde") == "abcd..."
    assert _mask_api_key("") == ""


def test_provider_registry_structure():
    """Registry has 3 entries with required fields."""
    assert len(PROVIDER_REGISTRY) == 3
    for entry in PROVIDER_REGISTRY:
        assert "id" in entry
        assert "label" in entry
        assert "description" in entry
        assert "requires_api_key" in entry
        assert "default_model" in entry
        assert "popular_models" in entry
    ids = {e["id"] for e in PROVIDER_REGISTRY}
    assert ids == {"ollama", "anthropic", "openai"}
