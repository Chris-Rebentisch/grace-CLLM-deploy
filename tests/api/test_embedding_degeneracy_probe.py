"""Startup embedding-degeneracy probe + F-006 lowercase contract.

F-006 (validation run ledger, 2026-07-02): a local Ollama nomic-embed-text
build returned byte-identical vectors for every proper-name-only input,
silently false-merging 21 of 44 entities on first import. Two defenses:

1. ``embed_texts`` lowercases input at the single choke point (workaround,
   similarity-preserving for all callers) — contract-tested here.
2. ``_probe_embedding_degeneracy`` in the API lifespan embeds two distinct
   names at startup and logs ``embeddings_backend_degenerate`` (error) when
   they come back identical — the failure mode is otherwise invisible.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# F-006 lowercase contract on embed_texts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_texts_lowercases_input_before_post():
    """The payload sent to /api/embed must be lowercased (F-006 workaround)."""
    from src.shared import embeddings as emb

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.1], [0.2]]}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            captured["json"] = json
            return _FakeResp()

    with patch.object(emb.httpx, "AsyncClient", return_value=_FakeClient()):
        await emb.embed_texts(
            ["Eleanor Vasquez", "Marcus WHITFIELD"], "http://localhost:11434"
        )

    assert captured["json"]["input"] == ["eleanor vasquez", "marcus whitfield"]


# ---------------------------------------------------------------------------
# Startup degeneracy probe
# ---------------------------------------------------------------------------


def _settings_stub():
    from unittest.mock import MagicMock

    s = MagicMock()
    s.ollama_base_url = "http://localhost:11434"
    return s


@pytest.mark.asyncio
async def test_probe_logs_error_on_identical_vectors():
    from src.api import main as api_main

    identical = [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5]]
    with (
        patch("src.shared.embeddings.embed_texts", new=AsyncMock(return_value=identical)),
        patch("src.shared.config.get_settings", return_value=_settings_stub()),
        patch.object(api_main, "logger") as mock_logger,
    ):
        await api_main._probe_embedding_degeneracy()

    assert mock_logger.error.called
    assert mock_logger.error.call_args[0][0] == "embeddings_backend_degenerate"


@pytest.mark.asyncio
async def test_probe_silent_on_distinct_vectors():
    from src.api import main as api_main

    distinct = [[0.1, 0.2, 0.3], [0.9, 0.8, 0.7]]
    with (
        patch("src.shared.embeddings.embed_texts", new=AsyncMock(return_value=distinct)),
        patch("src.shared.config.get_settings", return_value=_settings_stub()),
        patch.object(api_main, "logger") as mock_logger,
    ):
        await api_main._probe_embedding_degeneracy()

    assert not mock_logger.error.called


@pytest.mark.asyncio
async def test_probe_never_raises_when_backend_down():
    from src.api import main as api_main

    with (
        patch(
            "src.shared.embeddings.embed_texts",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ),
        patch("src.shared.config.get_settings", return_value=_settings_stub()),
    ):
        await api_main._probe_embedding_degeneracy()  # must not raise
