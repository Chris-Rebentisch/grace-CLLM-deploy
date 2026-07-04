"""Tests for Anthropic provider (mocked HTTP, no real API calls)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.anthropic_provider import AnthropicProvider


def _make_mock_resp(status_code, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


@pytest.fixture()
def provider():
    return AnthropicProvider(api_key="sk-ant-test-key-12345678", model="claude-haiku-4-5-20250414")


@pytest.mark.asyncio
async def test_generate_success(provider):
    """Mock 200 response, verify LLMResponse fields."""
    mock_data = {
        "content": [{"text": '{"answer": "hello"}'}],
        "model": "claude-haiku-4-5-20250414",
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, mock_data)
        mock_instance.post.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.generate("system", "user prompt")
        assert result.text == '{"answer": "hello"}'
        assert result.provider == "anthropic"
        assert result.model == "claude-haiku-4-5-20250414"


@pytest.mark.asyncio
async def test_generate_parses_usage(provider):
    """input_tokens and output_tokens from response."""
    mock_data = {
        "content": [{"text": "hi"}],
        "model": "claude-haiku-4-5-20250414",
        "usage": {"input_tokens": 100, "output_tokens": 25},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.generate("sys", "user", json_mode=False)
        assert result.input_tokens == 100
        assert result.output_tokens == 25


@pytest.mark.asyncio
async def test_generate_json_mode_appends_instruction(provider):
    """json_mode=True adds JSON instruction to prompt."""
    mock_data = {
        "content": [{"text": "{}"}],
        "model": "test",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        await provider.generate("sys", "user prompt", json_mode=True)

        call_args = mock_instance.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "ONLY valid JSON" in payload["messages"][0]["content"]


@pytest.mark.asyncio
async def test_generate_retry_on_429(provider):
    """Mock 429 then 200, verify retry."""
    mock_data = {
        "content": [{"text": "ok"}],
        "model": "test",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = [
            _make_mock_resp(429, headers={"retry-after": "0"}),
            _make_mock_resp(200, mock_data),
        ]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with patch("src.shared.anthropic_provider.asyncio.sleep", new_callable=AsyncMock):
            result = await provider.generate("sys", "user", json_mode=False)
            assert result.text == "ok"


@pytest.mark.asyncio
async def test_generate_retry_on_529(provider):
    """Mock 529 then 200, verify retry."""
    mock_data = {
        "content": [{"text": "ok"}],
        "model": "test",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = [
            _make_mock_resp(529, headers={"retry-after": "0"}),
            _make_mock_resp(200, mock_data),
        ]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with patch("src.shared.anthropic_provider.asyncio.sleep", new_callable=AsyncMock):
            result = await provider.generate("sys", "user", json_mode=False)
            assert result.text == "ok"


@pytest.mark.asyncio
async def test_generate_bad_api_key(provider):
    """Mock 401, verify clear error message."""
    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(401)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with pytest.raises(ValueError, match="invalid"):
            await provider.generate("sys", "user")


@pytest.mark.asyncio
async def test_health_check_success(provider):
    """Mock 200, healthy=True."""
    mock_data = {
        "content": [{"text": "hi"}],
        "model": "test",
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }

    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.health_check()
        assert result["healthy"] is True


@pytest.mark.asyncio
async def test_health_check_bad_key(provider):
    """Mock 401, healthy=False."""
    with patch("src.shared.anthropic_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(401)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.health_check()
        assert result["healthy"] is False
