"""Tests for OpenAI-compatible provider (mocked HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.openai_provider import OpenAICompatibleProvider


def _make_mock_resp(status_code, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


@pytest.fixture()
def provider():
    return OpenAICompatibleProvider(
        api_key="sk-test-key-12345678",
        model="gpt-4.1-nano",
        base_url="https://api.openai.com/v1",
    )


@pytest.mark.asyncio
async def test_generate_success(provider):
    """Mock 200, verify LLMResponse fields."""
    mock_data = {
        "choices": [{"message": {"content": '{"answer": "hello"}'}}],
        "model": "gpt-4.1-nano",
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    }

    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.generate("system", "user prompt")
        assert result.text == '{"answer": "hello"}'
        assert result.provider == "openai"
        assert result.input_tokens == 50
        assert result.output_tokens == 10


@pytest.mark.asyncio
async def test_generate_json_mode_sets_response_format(provider):
    """json_mode=True adds response_format to request."""
    mock_data = {
        "choices": [{"message": {"content": "{}"}}],
        "model": "gpt-4.1-nano",
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }

    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        await provider.generate("sys", "user", json_mode=True)

        call_args = mock_instance.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_generate_retry_on_429(provider):
    """Mock 429 then 200."""
    mock_data = {
        "choices": [{"message": {"content": "ok"}}],
        "model": "gpt-4.1-nano",
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }

    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = [
            _make_mock_resp(429, headers={"retry-after": "0"}),
            _make_mock_resp(200, mock_data),
        ]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with patch("src.shared.openai_provider.asyncio.sleep", new_callable=AsyncMock):
            result = await provider.generate("sys", "user", json_mode=False)
            assert result.text == "ok"


@pytest.mark.asyncio
async def test_generate_custom_base_url():
    """Request goes to custom base_url (DeepSeek test)."""
    provider = OpenAICompatibleProvider(
        api_key="sk-test",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
    )
    mock_data = {
        "choices": [{"message": {"content": "ok"}}],
        "model": "deepseek-chat",
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }

    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = _make_mock_resp(200, mock_data)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        await provider.generate("sys", "user", json_mode=False)

        call_args = mock_instance.post.call_args
        url = call_args.args[0] if call_args.args else call_args[0][0]
        assert "deepseek.com" in url


@pytest.mark.asyncio
async def test_health_check_models_endpoint(provider):
    """Mock /models, model found."""
    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, {
            "data": [{"id": "gpt-4.1-nano"}, {"id": "gpt-4.1-mini"}]
        })
        mock_instance.get.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.health_check()
        assert result["healthy"] is True
        assert result["model_available"] is True


@pytest.mark.asyncio
async def test_health_check_fallback_completion(provider):
    """Mock /models 404, falls back to tiny completion."""
    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get.return_value = _make_mock_resp(404)
        mock_instance.post.return_value = _make_mock_resp(200, {
            "choices": [{"message": {"content": "hi"}}],
            "model": "gpt-4.1-nano",
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.health_check()
        assert result["healthy"] is True


@pytest.mark.asyncio
async def test_health_check_bad_key(provider):
    """Mock 401, healthy=False."""
    with patch("src.shared.openai_provider.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get.return_value = _make_mock_resp(401)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await provider.health_check()
        assert result["healthy"] is False
