"""CP1 — WRITABLE_REVIEW_ROUTES frozenset, writable_review_tool decorator,
and http_client.call() three-way gate tests.

Covers:
- Frozenset disjointness with READONLY_ROUTES.
- Import-time assertion fires on writable_review_tool with bad tuple.
- http_client.call() dual-allowlist acceptance.
- Mismatched POST tuple rejection raises MCPReadOnlyViolation.
- GET verb bypass (no frozenset membership needed).
- GET verb bypass negative (POST not in allowlists still rejected).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.mcp_server.errors import MCPReadOnlyViolation
from src.mcp_server.server import (
    READONLY_ROUTES,
    WRITABLE_REVIEW_ROUTES,
    mcp,
    writable_review_tool,
)


def test_frozenset_disjoint():
    """WRITABLE_REVIEW_ROUTES and READONLY_ROUTES share zero entries."""
    assert len(WRITABLE_REVIEW_ROUTES & READONLY_ROUTES) == 0


def test_writable_frozenset_has_nine_entries():
    """Exactly nine mutating review routes in the frozenset (5 original + 4 Chunk 72a)."""
    assert len(WRITABLE_REVIEW_ROUTES) == 9


def test_writable_review_tool_valid_tuple():
    """Decorator accepts a tuple that IS in WRITABLE_REVIEW_ROUTES.

    Patch FastMCP.tool so we do not register a stray tool on the shared ``mcp``
    instance (order-independent contract with ``test_all_tools_registered``).
    """

    def _passthrough_tool_decorator_factory():
        def _decorator(fn):
            return fn

        return _decorator

    with patch.object(mcp, "tool", _passthrough_tool_decorator_factory):
        @writable_review_tool("POST", "/api/ontology/review/start")
        async def _dummy():
            pass

    assert _dummy.__grace_route__ == ("POST", "/api/ontology/review/start")


def test_writable_review_tool_invalid_tuple():
    """Decorator raises MCPReadOnlyViolation for a tuple NOT in the frozenset."""
    with pytest.raises(MCPReadOnlyViolation):

        @writable_review_tool("POST", "/api/some/unknown/route")
        async def _dummy():
            pass


def test_writable_review_tool_readonly_tuple_rejected():
    """A READONLY_ROUTES tuple is not accepted by writable_review_tool."""
    with pytest.raises(MCPReadOnlyViolation):

        @writable_review_tool("POST", "/api/retrieval/query")
        async def _dummy():
            pass


# --- http_client.call() three-way gate ---


@pytest.mark.asyncio
async def test_http_client_accepts_readonly_route(monkeypatch):
    """Routes in READONLY_ROUTES still accepted (regression)."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "POST",
        "/api/retrieval/query",
        tool="test",
        json_body={"query_text": "x", "top_k": 1},
    )
    # May fail for UPSTREAM reasons but should NOT be READONLY_VIOLATION
    assert result.get("code") != "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_accepts_writable_route(monkeypatch):
    """Routes in WRITABLE_REVIEW_ROUTES accepted by http_client.call()."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "POST",
        "/api/ontology/review/start",
        tool="test",
        json_body={},
    )
    assert result.get("code") != "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_rejects_unknown_post(monkeypatch):
    """POST to unknown route returns READONLY_VIOLATION envelope."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "POST",
        "/api/some/unknown/route",
        tool="test",
        json_body={},
    )
    assert result.get("code") == "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_get_verb_bypass(monkeypatch):
    """GET requests pass via verb bypass — no frozenset entry needed."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "GET",
        "/api/ontology/review/{session_id}/elements",
        tool="test",
        path_params={"session_id": "abc"},
    )
    # Should NOT be READONLY_VIOLATION — may be upstream error
    assert result.get("code") != "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_head_verb_bypass(monkeypatch):
    """HEAD requests pass via verb bypass."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "HEAD",
        "/api/some/arbitrary/path",
        tool="test",
    )
    assert result.get("code") != "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_options_verb_bypass(monkeypatch):
    """OPTIONS requests pass via verb bypass."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "OPTIONS",
        "/api/some/arbitrary/path",
        tool="test",
    )
    assert result.get("code") != "READONLY_VIOLATION"


@pytest.mark.asyncio
async def test_http_client_error_message_names_both_allowlists(monkeypatch):
    """Error message mentions both allowlists when neither matches."""
    from src.mcp_server import http_client

    monkeypatch.setattr(http_client, "_HOST", "127.0.0.1")
    result = await http_client.call(
        "POST",
        "/api/some/unknown",
        tool="test",
        json_body={},
    )
    assert "WRITABLE_REVIEW_ROUTES" in result.get("message", "")
    assert "READONLY_ROUTES" in result.get("message", "")
