"""Tests for generate_structured() on all three providers — CP3/CP4/CP5 of Chunk 63 (D444)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, Field

from src.shared.llm_provider import LLMResponse


# --- Test-local response model ---


class _SimpleResponse(BaseModel):
    """Test response model for generate_structured tests."""
    answer: str = Field(description="The answer")
    count: int = Field(default=0, description="A count")


# --- Helper for mocking record_llm_call ---

def _mock_llm_instrumentation():
    """Return a mock context manager for record_llm_call."""
    ctx = MagicMock()
    ctx.set_input_tokens = MagicMock()
    ctx.set_output_tokens = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# =============================================================================
# Ollama Provider Tests (CP3)
# =============================================================================


@pytest.mark.asyncio
async def test_ollama_tier_a_structured():
    """Tier A path: mocked Ollama returns schema-conformant JSON → .parsed is validated instance."""
    from src.discovery.ollama_client import OllamaProvider

    provider = OllamaProvider(model="test", base_url="http://localhost:11434")
    response_json = json.dumps({"answer": "hello", "count": 42})
    ollama_response = {
        "message": {"content": response_json},
        "model": "test",
        "total_duration": 100_000_000,
        "prompt_eval_count": 10,
        "eval_count": 20,
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = ollama_response

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.discovery.ollama_client.httpx.AsyncClient", return_value=mock_client),
        patch("src.discovery.ollama_client.OllamaProvider.generate_structured.__wrapped__", None, create=True) if False else patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result, LLMResponse)
    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "hello"
    assert result.parsed.count == 42


@pytest.mark.asyncio
async def test_ollama_tier_b_fallback_on_400():
    """Tier B fallback: mocked 400 on schema format → retries with 'json' → .parsed populated."""
    from src.discovery.ollama_client import OllamaProvider

    provider = OllamaProvider(model="test", base_url="http://localhost:11434")
    response_json = json.dumps({"answer": "fallback", "count": 1})

    # First call returns 400 (XGrammar rejection)
    mock_resp_400 = MagicMock()
    mock_resp_400.status_code = 400
    mock_resp_400.text = "unsupported schema"

    # Second call returns success
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.raise_for_status = MagicMock()
    mock_resp_ok.json.return_value = {
        "message": {"content": response_json},
        "model": "test",
        "total_duration": 50_000_000,
        "prompt_eval_count": 5,
        "eval_count": 10,
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_400, mock_resp_ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.discovery.ollama_client.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "fallback"


@pytest.mark.asyncio
async def test_ollama_transport_error_raises():
    """Transport error: mocked connection error → raises httpx.ConnectError."""
    from src.discovery.ollama_client import OllamaProvider

    provider = OllamaProvider(model="test", base_url="http://localhost:11434")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.discovery.ollama_client.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        with pytest.raises(httpx.ConnectError):
            await provider.generate_structured(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleResponse,
            )


# =============================================================================
# OpenAI-Compatible Provider Tests (CP4)
# =============================================================================


@pytest.mark.asyncio
async def test_openai_tier_a_structured():
    """Tier A: mocked OpenAI-compatible returns conformant JSON → .parsed validated."""
    from src.shared.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="test-key", model="gpt-4", base_url="https://api.openai.com/v1"
    )
    response_json = json.dumps({"answer": "structured", "count": 7})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": response_json}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "model": "gpt-4",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.openai_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "structured"
    assert result.parsed.count == 7


@pytest.mark.asyncio
async def test_openai_cached_downgrade_on_400():
    """Cached downgrade: first call gets 400 → Tier B; second call goes straight to Tier B."""
    from src.shared.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="test-key", model="deepseek-chat", base_url="https://api.deepseek.com/v1"
    )
    assert not provider._structured_tier_b

    response_json = json.dumps({"answer": "tier-b", "count": 0})

    # First call: 400 on Tier A, then success on Tier B
    mock_resp_400 = MagicMock()
    mock_resp_400.status_code = 400
    mock_resp_400.text = "json_schema not supported"

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.raise_for_status = MagicMock()
    mock_resp_ok.json.return_value = {
        "choices": [{"message": {"content": response_json}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        "model": "deepseek-chat",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_400, mock_resp_ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.openai_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert provider._structured_tier_b is True  # cached

    # Second call: should go straight to Tier B (only one request needed)
    mock_client2 = AsyncMock()
    mock_client2.post = AsyncMock(return_value=mock_resp_ok)
    mock_client2.__aenter__ = AsyncMock(return_value=mock_client2)
    mock_client2.__aexit__ = AsyncMock(return_value=False)

    ctx2 = _mock_llm_instrumentation()

    with (
        patch("src.shared.openai_provider.httpx.AsyncClient", return_value=mock_client2),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx2),
    ):
        result2 = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result2.parsed, _SimpleResponse)
    # Should have made exactly one request (no Tier A probe)
    assert mock_client2.post.call_count == 1


@pytest.mark.asyncio
async def test_openai_config_override_tier_b():
    """Config override tier_b: skips Tier A probe entirely."""
    from src.shared.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="test-key", model="gpt-4",
        base_url="https://api.openai.com/v1",
        structured_output="tier_b",
    )

    response_json = json.dumps({"answer": "forced-b", "count": 3})

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.raise_for_status = MagicMock()
    mock_resp_ok.json.return_value = {
        "choices": [{"message": {"content": response_json}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        "model": "gpt-4",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp_ok)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.openai_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "forced-b"
    # Only one request (no Tier A attempt)
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_openai_d36_private_network_no_key():
    """D36 key-skip: private-network URL provider → no API key required (unchanged behavior)."""
    from src.shared.llm_provider import _is_private_network_url

    assert _is_private_network_url("http://127.0.0.1:8080") is True
    assert _is_private_network_url("http://192.168.1.100:11434") is True
    assert _is_private_network_url("https://api.openai.com/v1") is False


# =============================================================================
# Anthropic Provider Tests (CP5)
# =============================================================================


@pytest.mark.asyncio
async def test_anthropic_tier_a_structured():
    """Tier A: mocked Anthropic returns conformant JSON → .parsed validated."""
    from src.shared.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001")
    response_json = json.dumps({"answer": "anthropic-ok", "count": 99})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"text": response_json}],
        "usage": {"input_tokens": 15, "output_tokens": 25},
        "model": "claude-haiku-4-5-20251001",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.anthropic_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "anthropic-ok"
    assert result.parsed.count == 99


@pytest.mark.asyncio
async def test_anthropic_compilation_fallback():
    """Compilation fallback: mocked 400 with schema error → Tier B → .parsed populated."""
    from src.shared.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001")
    response_json = json.dumps({"answer": "fallback-ok", "count": 1})

    mock_resp_400 = MagicMock()
    mock_resp_400.status_code = 400
    mock_resp_400.text = '{"error": {"message": "Invalid schema: too complex"}}'

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.raise_for_status = MagicMock()
    mock_resp_ok.json.return_value = {
        "content": [{"text": response_json}],
        "usage": {"input_tokens": 10, "output_tokens": 15},
        "model": "claude-haiku-4-5-20251001",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[mock_resp_400, mock_resp_ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.anthropic_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        result = await provider.generate_structured(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleResponse,
        )

    assert isinstance(result.parsed, _SimpleResponse)
    assert result.parsed.answer == "fallback-ok"


@pytest.mark.asyncio
async def test_anthropic_transport_error_raises():
    """Transport error: raises."""
    from src.shared.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    ctx = _mock_llm_instrumentation()

    with (
        patch("src.shared.anthropic_provider.httpx.AsyncClient", return_value=mock_client),
        patch("src.analytics.llm_instrumentation.record_llm_call", return_value=ctx),
    ):
        with pytest.raises(httpx.ConnectError):
            await provider.generate_structured(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleResponse,
            )
