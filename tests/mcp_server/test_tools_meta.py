"""Spec §11.2 item 10 — happy-path test for ``grace_explain_capabilities``.

The meta tool has no HTTP leg. The test patches
``httpx.AsyncClient`` with a side effect that would fail loudly if
the tool tried to call it — any HTTP traffic from this tool is a bug.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_grace_explain_capabilities():
    from src.mcp_server.tools_meta import grace_explain_capabilities

    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient",
        side_effect=AssertionError(
            "meta tool must not make HTTP calls"
        ),
    ):
        result = await grace_explain_capabilities()

    assert isinstance(result, str)
    assert len(result) > 0
    # Soft token cap — the meta summary should be short enough to
    # leave plenty of the MCP host's context window for real work.
    assert len(result) < 5000
    # Sanity: mentions at least one other tool name.
    assert "grace_" in result
