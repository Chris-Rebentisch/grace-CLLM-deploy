"""Configurable timeout for src.shared.embeddings.embed_texts.

The httpx timeout was hardcoded to 120s. It is now a per-call parameter
(default unchanged at 120) with an optional GRACE_EMBED_TIMEOUT_SECONDS
env-var default override.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.shared.embeddings import embed_texts


def _run_embed(captured: dict, **kwargs) -> list[list[float]]:
    async def mock_post(url, json=None, **kw):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"embeddings": [[0.1, 0.2]]})
        return resp

    def client_factory(*args, **client_kwargs):
        captured.update(client_kwargs)
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    with patch("src.shared.embeddings.httpx.AsyncClient", side_effect=client_factory):
        return asyncio.run(
            embed_texts(["hello"], base_url="http://localhost:11434", **kwargs)
        )


def test_default_timeout_is_120(monkeypatch) -> None:
    monkeypatch.delenv("GRACE_EMBED_TIMEOUT_SECONDS", raising=False)
    captured: dict = {}
    result = _run_embed(captured)
    assert captured["timeout"] == 120.0
    assert result == [[0.1, 0.2]]


def test_explicit_timeout_parameter_wins(monkeypatch) -> None:
    monkeypatch.setenv("GRACE_EMBED_TIMEOUT_SECONDS", "45")
    captured: dict = {}
    _run_embed(captured, timeout=7.5)
    assert captured["timeout"] == 7.5


def test_env_var_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("GRACE_EMBED_TIMEOUT_SECONDS", "300")
    captured: dict = {}
    _run_embed(captured)
    assert captured["timeout"] == 300.0


def test_invalid_env_var_falls_back_to_120(monkeypatch) -> None:
    monkeypatch.setenv("GRACE_EMBED_TIMEOUT_SECONDS", "not-a-number")
    captured: dict = {}
    _run_embed(captured)
    assert captured["timeout"] == 120.0
