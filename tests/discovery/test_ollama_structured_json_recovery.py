"""Guarded json.loads on the XGrammar success paths (ollama_client).

XGrammar normally guarantees valid JSON, but the success paths previously
called ``json.loads(raw_text)`` unguarded — any malformed content leaked an
unhandled ``JSONDecodeError``. They now fall back to ``_parse_json_robust``
and raise a typed ``ValueError`` (Tier-B parity) only when recovery fails.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from src.discovery.ollama_client import OllamaProvider


class _Item(BaseModel):
    name: str


def _make_tiny_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color="red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def _mock_record_llm_call():
    """Patch record_llm_call context manager to no-op."""
    mock_ctx = MagicMock()
    mock_ctx.set_input_tokens = MagicMock()
    mock_ctx.set_output_tokens = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "src.analytics.llm_instrumentation.record_llm_call", return_value=mock_ctx
    ):
        yield


def _mock_httpx_client(content: str):
    """Build an httpx.AsyncClient patch whose post() returns 200 + content."""

    async def mock_post(url, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={
                "message": {"content": content},
                "model": "qwen2.5:7b",
                "prompt_eval_count": 10,
                "eval_count": 5,
                "total_duration": 1_000_000_000,
            }
        )
        return resp

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _run_structured(content: str):
    provider = OllamaProvider(model="qwen2.5:7b")
    with patch("httpx.AsyncClient") as cls:
        cls.return_value = _mock_httpx_client(content)
        return asyncio.run(
            provider.generate_structured("sys", "user", response_model=_Item)
        )


def test_wellformed_json_unchanged(_mock_record_llm_call) -> None:
    result = _run_structured('{"name": "alpha"}')
    assert result.parsed == _Item(name="alpha")
    assert result.text == '{"name": "alpha"}'


def test_fenced_json_is_recovered(_mock_record_llm_call) -> None:
    result = _run_structured('```json\n{"name": "beta"}\n```')
    assert result.parsed == _Item(name="beta")


def test_unrecoverable_garbage_raises_typed_valueerror(
    _mock_record_llm_call,
) -> None:
    with pytest.raises(ValueError, match="JSON parse failed"):
        _run_structured("this is not json at all")


def _run_vision(content: str):
    provider = OllamaProvider(model="qwen2.5:7b")
    with patch(
        "src.shared.llm_provider.read_vision_config_from_yaml",
        return_value={"model": "qwen2.5-vl:7b", "enabled": True},
    ):
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _mock_httpx_client(content)
            return asyncio.run(
                provider.generate_vision(
                    "describe", [_make_tiny_jpeg()], response_model=_Item
                )
            )


def test_vision_fenced_json_is_recovered(_mock_record_llm_call) -> None:
    result = _run_vision('```json\n{"name": "gamma"}\n```')
    assert result.parsed == _Item(name="gamma")


def test_vision_unrecoverable_garbage_raises_typed_valueerror(
    _mock_record_llm_call,
) -> None:
    with pytest.raises(ValueError, match="JSON parse failed"):
        _run_vision("nope, not json")
