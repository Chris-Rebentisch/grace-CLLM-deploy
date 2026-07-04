"""Tests for D36 no-auth fix: private network URL detection in LLM provider."""

import pytest

from src.shared.llm_provider import _is_private_network_url, get_provider


class TestIsPrivateNetworkUrl:
    """Tests for _is_private_network_url utility."""

    def test_localhost(self):
        assert _is_private_network_url("http://localhost:11434") is True

    def test_127_0_0_1(self):
        assert _is_private_network_url("http://127.0.0.1:8080") is True

    def test_ipv6_loopback(self):
        assert _is_private_network_url("http://[::1]:11434") is True

    def test_192_168_range(self):
        assert _is_private_network_url("http://192.168.1.100:8080") is True

    def test_10_range(self):
        assert _is_private_network_url("http://10.0.0.5:8080") is True

    def test_172_16_range(self):
        assert _is_private_network_url("http://172.16.0.1:8080") is True

    def test_172_31_range(self):
        assert _is_private_network_url("http://172.31.255.255:8080") is True

    def test_cloud_openai(self):
        assert _is_private_network_url("https://api.openai.com/v1") is False

    def test_cloud_deepseek(self):
        assert _is_private_network_url("https://api.deepseek.com/v1") is False

    def test_empty_url(self):
        assert _is_private_network_url("") is False


class TestGetProviderD36:
    """Tests for get_provider with D36 no-auth fix."""

    def test_openai_no_key_localhost_no_raise(self):
        """No API key + localhost base_url should not raise."""
        provider = get_provider(config_override={
            "provider": "openai",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "timeout": 300,
        })
        assert provider.provider_name == "openai"
        assert provider.api_key == "no-key"

    def test_openai_no_key_cloud_raises(self):
        """No API key + cloud base_url should raise ValueError."""
        with pytest.raises(ValueError, match="requires LLM_API_KEY"):
            get_provider(config_override={
                "provider": "openai",
                "model": "gpt-4.1-nano",
                "base_url": "https://api.openai.com/v1",
                "api_key": "",
                "timeout": 300,
            })

    def test_openai_with_key_cloud_succeeds(self):
        """API key + cloud URL should work (existing behavior unchanged)."""
        provider = get_provider(config_override={
            "provider": "openai",
            "model": "gpt-4.1-nano",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-key-12345678",
            "timeout": 300,
        })
        assert provider.provider_name == "openai"
        assert provider.api_key == "sk-test-key-12345678"

    def test_openai_no_key_private_192_168(self):
        """No API key + 192.168.x.x should not raise."""
        provider = get_provider(config_override={
            "provider": "openai",
            "model": "llama3:8b",
            "base_url": "http://192.168.1.50:8080/v1",
            "api_key": "",
            "timeout": 300,
        })
        assert provider.provider_name == "openai"
