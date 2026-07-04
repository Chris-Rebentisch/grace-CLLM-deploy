"""Tests for Ollama client (mock HTTP, no real LLM calls)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.discovery.ollama_client import (
    OllamaConfig,
    OllamaResponse,
    _parse_json_robust,
    check_ollama_health,
    generate,
)


@pytest.fixture()
def config():
    return OllamaConfig(base_url="http://localhost:11434", model="qwen2.5:7b")


def _make_mock_resp(status_code, json_data=None, text=""):
    """Create a mock httpx Response with sync .json() and .raise_for_status()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_parse_json_robust_direct():
    """Direct JSON parsing works."""
    result = _parse_json_robust('[{"question": "test?"}]')
    assert isinstance(result, list)
    assert result[0]["question"] == "test?"


def test_parse_json_robust_markdown_fences():
    """Strips markdown code fences."""
    result = _parse_json_robust('```json\n[{"question": "test?"}]\n```')
    assert isinstance(result, list)


def test_parse_json_robust_extract_brackets():
    """Extracts JSON from surrounding text."""
    result = _parse_json_robust('Here is the output:\n[{"question": "test?"}]\nDone.')
    assert isinstance(result, list)


def test_parse_json_robust_jsonl():
    """Parses JSONL (one object per line)."""
    result = _parse_json_robust('{"question": "q1?"}\n{"question": "q2?"}')
    assert isinstance(result, list)
    assert len(result) == 2


def test_parse_json_robust_failure():
    """Returns None for unparseable text."""
    result = _parse_json_robust("This is not JSON at all")
    assert result is None


@pytest.mark.asyncio
async def test_generate_json_response(config):
    """Mock Ollama HTTP response, verify JSON parsing."""
    mock_response_data = {
        "message": {"content": '[{"question": "What insurance policies exist?", "cq_type": "SCOPING", "rationale": "test", "source_document_names": [], "priority": "HIGH"}]'},
        "model": "qwen2.5:7b",
        "total_duration": 5000000000,
        "prompt_eval_count": 100,
        "eval_count": 50,
    }

    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, mock_response_data)
        mock_instance.post.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await generate("test prompt", config=config)
        assert result.parsed_json is not None
        assert isinstance(result.parsed_json, list)
        assert result.parsed_json[0]["question"] == "What insurance policies exist?"
        assert result.model == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_generate_retry_on_error(config):
    """Mock 500 response, verify retry behavior."""
    config.max_retries = 1

    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp_500 = _make_mock_resp(500, text="Internal Server Error")
        mock_resp_200 = _make_mock_resp(200, {
            "message": {"content": "[]"},
            "model": "qwen2.5:7b",
            "total_duration": 1000000000,
            "prompt_eval_count": 10,
            "eval_count": 5,
        })

        mock_instance.post.side_effect = [mock_resp_500, mock_resp_200]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await generate("test", config=config)
        assert result.parsed_json == []


@pytest.mark.asyncio
async def test_generate_timeout(config):
    """Mock timeout, verify error handling."""
    import httpx as httpx_mod

    config.max_retries = 0

    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = httpx_mod.TimeoutException("timeout")
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with pytest.raises(RuntimeError, match="timed out"):
            await generate("test", config=config)


@pytest.mark.asyncio
async def test_health_check_healthy(config):
    """Mock /api/tags with target model present."""
    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, {
            "models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]
        })
        mock_instance.get.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await check_ollama_health(config)
        assert result["healthy"] is True
        assert result["model_available"] is True


@pytest.mark.asyncio
async def test_health_check_model_missing(config):
    """Mock /api/tags without target model."""
    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, {
            "models": [{"name": "llama3:8b"}]
        })
        mock_instance.get.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await check_ollama_health(config)
        assert result["healthy"] is True
        assert result["model_available"] is False


@pytest.mark.asyncio
async def test_temperature_zero(config):
    """Verify temperature=0.0 in request body."""
    with patch("src.discovery.ollama_client.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_resp = _make_mock_resp(200, {
            "message": {"content": "[]"},
            "model": "qwen2.5:7b",
            "total_duration": 1000000000,
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        mock_instance.post.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        await generate("test", config=config)

        call_args = mock_instance.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["options"]["temperature"] == 0.0
