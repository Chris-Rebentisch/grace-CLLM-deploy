"""CP4 — Session-lifecycle MCP tool tests (D365).

Covers:
- grace_session_start returns valid session_id.
- grace_session_advance_phase transitions phase.
- grace_session_close invokes close-summary then close-confirm in sequence.
- Tools registered in WRITABLE_REVIEW_ROUTES.
- Tool descriptions are static string literals (covered by AST scan).
"""

from __future__ import annotations

import pytest

from src.mcp_server.server import WRITABLE_REVIEW_ROUTES


def test_session_start_route_binding():
    """grace_session_start binds to review/start route."""
    from src.mcp_server.tools_session import grace_session_start

    fn = grace_session_start
    route = getattr(fn, "__grace_route__", None)
    assert route == ("POST", "/api/ontology/review/start")
    assert route in WRITABLE_REVIEW_ROUTES


def test_session_advance_phase_route_binding():
    """grace_session_advance_phase binds to elicitation/events route."""
    from src.mcp_server.tools_session import grace_session_advance_phase

    fn = grace_session_advance_phase
    route = getattr(fn, "__grace_route__", None)
    assert route == ("POST", "/api/elicitation/events")
    assert route in WRITABLE_REVIEW_ROUTES


def test_session_close_route_binding():
    """grace_session_close binds to close-summary route (primary)."""
    from src.mcp_server.tools_session import grace_session_close

    fn = grace_session_close
    route = getattr(fn, "__grace_route__", None)
    assert route == ("POST", "/api/regeneration/close-summary")
    assert route in WRITABLE_REVIEW_ROUTES


def test_close_confirm_also_in_writable_routes():
    """close-confirm tuple is also in WRITABLE_REVIEW_ROUTES (runtime validation)."""
    assert ("POST", "/api/regeneration/close-confirm") in WRITABLE_REVIEW_ROUTES


def test_session_start_has_permission_gate():
    """grace_session_start is permission-gated."""
    from src.mcp_server.tools_session import grace_session_start

    fn = grace_session_start
    gate = getattr(fn, "__grace_permission_gate__", None)
    assert gate is not None


def test_session_advance_phase_has_permission_gate():
    """grace_session_advance_phase is permission-gated."""
    from src.mcp_server.tools_session import grace_session_advance_phase

    fn = grace_session_advance_phase
    gate = getattr(fn, "__grace_permission_gate__", None)
    assert gate is not None


def test_session_close_has_permission_gate():
    """grace_session_close is permission-gated."""
    from src.mcp_server.tools_session import grace_session_close

    fn = grace_session_close
    gate = getattr(fn, "__grace_permission_gate__", None)
    assert gate is not None


def test_session_start_has_docstring():
    """grace_session_start has a static docstring."""
    from src.mcp_server.tools_session import grace_session_start

    fn = grace_session_start
    doc = fn.__doc__
    assert doc is not None
    assert len(doc.strip()) > 0


def test_session_close_has_docstring():
    """grace_session_close has a static docstring."""
    from src.mcp_server.tools_session import grace_session_close

    fn = grace_session_close
    doc = fn.__doc__
    assert doc is not None
    assert len(doc.strip()) > 0


@pytest.mark.asyncio
async def test_session_close_calls_two_routes(monkeypatch):
    """grace_session_close makes two HTTP calls in order."""
    from src.mcp_server import http_client
    from src.mcp_server.tools_session import grace_session_close

    calls: list[tuple[str, str]] = []
    original_call = http_client.call

    async def mock_call(method, path, **kwargs):
        calls.append((method, path))
        return {"session_id": "test-id", "status": "ok"}

    monkeypatch.setattr(http_client, "call", mock_call)

    # Bypass the permission gate for testing.
    inner_fn = grace_session_close
    while hasattr(inner_fn, "__wrapped__"):
        inner_fn = inner_fn.__wrapped__

    # Directly call the underlying function to skip permission gating.
    from src.mcp_server.tools_session import grace_session_close as _fn

    # Reset the calls and invoke via a direct http_client mock
    calls.clear()
    monkeypatch.setattr("src.mcp_server.tools_session.http_client", type("FakeModule", (), {"call": staticmethod(mock_call)})())

    # Import the raw async function
    import src.mcp_server.tools_session as mod
    result = await mod.grace_session_close.__wrapped__("test-session")

    assert len(calls) == 2
    assert calls[0] == ("POST", "/api/regeneration/close-summary")
    assert calls[1] == ("POST", "/api/regeneration/close-confirm")


@pytest.mark.asyncio
async def test_session_start_returns_message(monkeypatch):
    """grace_session_start returns confirmation message."""
    from src.mcp_server import http_client

    async def mock_call(method, path, **kwargs):
        return {"session_id": "abc-123"}

    monkeypatch.setattr("src.mcp_server.tools_session.http_client", type("FakeModule", (), {"call": staticmethod(mock_call)})())

    import src.mcp_server.tools_session as mod
    result = await mod.grace_session_start.__wrapped__("prepare")

    assert result["session_id"] == "abc-123"
    assert "Session started" in result["message"]
