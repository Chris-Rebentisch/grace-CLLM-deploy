"""Tests for ExtractionLLMClient — Instructor wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionResult
from src.extraction.instructor_client import (
    ExtractionLLMClient,
    ExtractionLLMError,
    _build_provider_string,
)


class TestBuildProviderString:
    """Tests for provider string construction."""

    def test_ollama(self):
        assert _build_provider_string("ollama", "qwen2.5:7b") == "ollama/qwen2.5:7b"

    def test_anthropic(self):
        assert _build_provider_string("anthropic", "claude-haiku-4-5-20251001") == "anthropic/claude-haiku-4-5-20251001"

    def test_openai(self):
        assert _build_provider_string("openai", "gpt-4.1-nano") == "openai/gpt-4.1-nano"

    def test_unknown_raises(self):
        with pytest.raises(ExtractionLLMError, match="Unknown provider"):
            _build_provider_string("fakeprovider", "model")


class TestExtractionLLMClientInit:
    """Tests for client initialization with various configs."""

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_default_config_ollama(self, mock_settings, mock_yaml, mock_from_provider):
        """Client creates with default config (Ollama provider)."""
        mock_yaml.return_value = {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        assert client._extraction_provider == "ollama"
        assert client._extraction_model == "qwen2.5:7b"
        assert client.extraction_provider == "ollama"
        assert client.extraction_model == "qwen2.5:7b"
        assert client.verification_provider == "ollama"
        assert client.verification_model == "qwen2.5:7b"
        # from_provider called three times (extraction + verification + resolve)
        assert mock_from_provider.call_count == 3

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_anthropic_override(self, mock_settings, mock_yaml, mock_from_provider):
        """Client creates with Anthropic provider override."""
        mock_yaml.return_value = {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="sk-ant-test")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings(
            extraction_provider="anthropic",
            extraction_model="claude-haiku-4-5-20251001",
        )
        client = ExtractionLLMClient(config)

        assert client._extraction_provider == "anthropic"
        assert client._extraction_model == "claude-haiku-4-5-20251001"
        # Verification falls back to global (ollama)
        assert client._verification_provider == "ollama"

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_openai_override(self, mock_settings, mock_yaml, mock_from_provider):
        """Client creates with OpenAI-compatible provider override."""
        mock_yaml.return_value = {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "https://api.openai.com/v1",
            "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="sk-openai-test")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings(
            extraction_provider="openai",
            extraction_model="gpt-4.1-nano",
        )
        client = ExtractionLLMClient(config)

        assert client._extraction_provider == "openai"
        assert client._extraction_model == "gpt-4.1-nano"

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_separate_extraction_and_verification(self, mock_settings, mock_yaml, mock_from_provider):
        """Client creates with separate extraction and verification providers."""
        mock_yaml.return_value = {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="sk-ant-test")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings(
            extraction_provider="anthropic",
            extraction_model="claude-haiku-4-5-20251001",
            verification_provider="ollama",
            verification_model="qwen2.5:7b",
        )
        client = ExtractionLLMClient(config)

        assert client._extraction_provider == "anthropic"
        assert client._verification_provider == "ollama"

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_fallback_to_global(self, mock_settings, mock_yaml, mock_from_provider):
        """Client falls back to global config when fields are None."""
        mock_yaml.return_value = {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings()  # all None
        client = ExtractionLLMClient(config)

        # Both should use global ollama config
        assert client._extraction_provider == "ollama"
        assert client._verification_provider == "ollama"


class TestExtractionLLMClientMethods:
    """Tests for extract() and verify() methods."""

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_extract_returns_model(self, mock_settings, mock_yaml, mock_from_provider):
        """extract() returns validated Pydantic model."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        result = await client.extract("system", "user", ExtractionResult)
        assert isinstance(result, ExtractionResult)

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_extract_raises_on_timeout(self, mock_settings, mock_yaml, mock_from_provider):
        """extract() raises ExtractionLLMError on timeout."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=TimeoutError("timed out"))
        mock_from_provider.return_value = mock_client

        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        with pytest.raises(ExtractionLLMError, match="Extraction call failed"):
            await client.extract("system", "user", ExtractionResult)

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_extract_raises_on_validation(self, mock_settings, mock_yaml, mock_from_provider):
        """extract() raises ExtractionLLMError on validation exhaustion."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ValueError("Validation failed after retries")
        )
        mock_from_provider.return_value = mock_client

        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        with pytest.raises(ExtractionLLMError):
            await client.extract("system", "user", ExtractionResult)

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_verify_uses_verification_client(self, mock_settings, mock_yaml, mock_from_provider):
        """verify() uses the verification client, not extraction client."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        expected = ExtractionResult(entities=[], relationships=[])
        mock_ext_client = MagicMock()
        mock_ext_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_ver_client = MagicMock()
        mock_ver_client.chat.completions.create = AsyncMock(return_value=expected)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_ext_client
            return mock_ver_client

        mock_from_provider.side_effect = side_effect

        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        await client.verify("system", "user", ExtractionResult)
        mock_ver_client.chat.completions.create.assert_called_once()
        mock_ext_client.chat.completions.create.assert_not_called()


class TestTimeoutPropagation:
    """Tests for timeout wiring to Instructor create() calls."""

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_extract_passes_extraction_timeout(self, mock_settings, mock_yaml, mock_from_provider):
        """extract() passes timeout=extraction_timeout to create()."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        config = ExtractionSettings(extraction_timeout=90.0)
        client = ExtractionLLMClient(config)

        await client.extract("system", "user", ExtractionResult)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["timeout"] == 90.0

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_verify_passes_verification_timeout(self, mock_settings, mock_yaml, mock_from_provider):
        """verify() passes timeout=verification_timeout to create()."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="")

        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        config = ExtractionSettings(verification_timeout=45.0)
        client = ExtractionLLMClient(config)

        await client.verify("system", "user", ExtractionResult)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["timeout"] == 45.0


class TestProviderBaseUrlOverride:
    """Tests for base_url override and warning logic."""

    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_override_url_used(self, mock_settings, mock_yaml, mock_from_provider):
        """Provider override with explicit base_url routes to override URL."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="sk-test")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings(
            extraction_provider="openai",
            extraction_model="gpt-4.1-nano",
            extraction_base_url="http://localhost:8080/v1",
        )
        ExtractionLLMClient(config)

        # First from_provider call is extraction
        ext_call_kwargs = mock_from_provider.call_args_list[0].kwargs
        assert ext_call_kwargs["base_url"] == "http://localhost:8080/v1"

    @patch("src.extraction.instructor_client.log")
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    def test_provider_override_without_base_url_warns(
        self, mock_settings, mock_yaml, mock_from_provider, mock_log
    ):
        """Provider override to openai without base_url emits warning when global URL is Ollama."""
        mock_yaml.return_value = {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434", "timeout": 300,
        }
        mock_settings.return_value = MagicMock(llm_api_key="sk-test")
        mock_from_provider.return_value = MagicMock()

        config = ExtractionSettings(
            extraction_provider="openai",
            extraction_model="gpt-4.1-nano",
        )
        ExtractionLLMClient(config)

        mock_log.warning.assert_called()
        warning_args = mock_log.warning.call_args
        assert "extraction_base_url not set" in warning_args.args[0]


def _mock_env(mock_settings, mock_yaml, provider="ollama"):
    """Wire the standard yaml/settings mocks for client construction."""
    mock_yaml.return_value = {
        "provider": provider,
        "model": "qwen2.5:7b" if provider == "ollama" else "claude-sonnet-5",
        "base_url": "http://localhost:11434",
        "timeout": 300,
    }
    mock_settings.return_value = MagicMock(llm_api_key="sk-test")


class TestF0015TemperatureHandling:
    """F-0015(a) / ISS-0031: omit temperature when None; retry once on 400."""

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_temperature_sent_by_default(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """Default config (temperature=0.0) still sends the parameter."""
        _mock_env(mock_settings, mock_yaml)
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings())
        await client.extract("system", "user", ExtractionResult)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_temperature_omitted_when_none(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """temperature=None omits the kwarg entirely from the request."""
        _mock_env(mock_settings, mock_yaml)
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings(temperature=None))
        await client.extract("system", "user", ExtractionResult)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_verify_and_resolve_omit_when_none(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """verification_temperature/er_temperature None also omit the kwarg."""
        _mock_env(mock_settings, mock_yaml)
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(
            ExtractionSettings(verification_temperature=None, er_temperature=None)
        )
        await client.verify("system", "user", ExtractionResult)
        assert "temperature" not in mock_client.chat.completions.create.call_args.kwargs

        await client.resolve(ExtractionResult, [{"role": "user", "content": "x"}])
        assert "temperature" not in mock_client.chat.completions.create.call_args.kwargs

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_retry_once_without_temperature_on_400(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """A 400 naming temperature triggers exactly one retry without it."""
        _mock_env(mock_settings, mock_yaml, provider="anthropic")
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                Exception(
                    "Error code: 400 - {'type': 'error', 'error': "
                    "{'type': 'invalid_request_error', 'message': "
                    "'`temperature` is deprecated for this model'}}"
                ),
                expected,
            ]
        )
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings())
        result = await client.extract("system", "user", ExtractionResult)

        assert isinstance(result, ExtractionResult)
        assert mock_client.chat.completions.create.call_count == 2
        first_kwargs = mock_client.chat.completions.create.call_args_list[0].kwargs
        retry_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
        assert "temperature" in first_kwargs
        assert "temperature" not in retry_kwargs
        # Everything else preserved on the retry
        assert retry_kwargs["max_tokens"] == first_kwargs["max_tokens"]
        assert retry_kwargs["timeout"] == first_kwargs["timeout"]

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_retry_is_bounded(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """A second temperature-400 propagates — no infinite retry loop."""
        _mock_env(mock_settings, mock_yaml, provider="anthropic")
        err = Exception(
            "Error code: 400 - temperature is deprecated for this model"
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[err, err])
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings())
        with pytest.raises(ExtractionLLMError, match="Extraction call failed"):
            await client.extract("system", "user", ExtractionResult)
        assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_unrelated_error_not_retried(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        """Errors that don't name temperature are not retried."""
        _mock_env(mock_settings, mock_yaml)
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 400 - max_tokens too large")
        )
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings())
        with pytest.raises(ExtractionLLMError):
            await client.extract("system", "user", ExtractionResult)
        assert mock_client.chat.completions.create.call_count == 1


class TestF0015ProviderAwareMaxTokens:
    """F-0015(c) / ISS-0031: extract() uses the provider-aware ceiling."""

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_ollama_extract_uses_4096(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        _mock_env(mock_settings, mock_yaml, provider="ollama")
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(ExtractionSettings())
        await client.extract("system", "user", ExtractionResult)
        assert mock_client.chat.completions.create.call_args.kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_anthropic_extract_uses_8192(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        _mock_env(mock_settings, mock_yaml)
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(
            ExtractionSettings(
                extraction_provider="anthropic",
                extraction_model="claude-sonnet-5",
            )
        )
        await client.extract("system", "user", ExtractionResult)
        assert mock_client.chat.completions.create.call_args.kwargs["max_tokens"] == 8192

    @pytest.mark.asyncio
    @patch("src.extraction.instructor_client.instructor.from_provider")
    @patch("src.extraction.instructor_client.read_llm_config_from_yaml")
    @patch("src.extraction.instructor_client.get_settings")
    async def test_explicit_config_overrides_provider_default(
        self, mock_settings, mock_yaml, mock_from_provider
    ):
        _mock_env(mock_settings, mock_yaml)
        expected = ExtractionResult(entities=[], relationships=[])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)
        mock_from_provider.return_value = mock_client

        client = ExtractionLLMClient(
            ExtractionSettings(
                extraction_provider="anthropic",
                extraction_model="claude-sonnet-5",
                max_output_tokens=3000,
            )
        )
        await client.extract("system", "user", ExtractionResult)
        assert mock_client.chat.completions.create.call_args.kwargs["max_tokens"] == 3000


class TestExtractionLLMError:
    """Tests for ExtractionLLMError exception."""

    def test_attributes(self):
        err = ExtractionLLMError(
            "test error", provider="ollama", model="qwen2.5:7b", retries_attempted=3
        )
        assert str(err) == "test error"
        assert err.provider == "ollama"
        assert err.model == "qwen2.5:7b"
        assert err.retries_attempted == 3
