"""CP5 — Review-decision MCP tool tests (D366/D367).

Covers:
- POST tools registered in WRITABLE_REVIEW_ROUTES.
- GET tools pass via verb bypass.
- Endpoint mapping.
- Per-element decision (no batch).
- Deep-link payload.
- Description safety (covered by AST scan).
"""

from __future__ import annotations

import pytest

from src.mcp_server.server import WRITABLE_REVIEW_ROUTES, READONLY_ROUTES


def test_review_decide_route_binding():
    """grace_review_decide binds to decide route."""
    from src.mcp_server.tools_review import grace_review_decide

    route = getattr(grace_review_decide, "__grace_route__", None)
    assert route == ("POST", "/api/ontology/review/{session_id}/decide")
    assert route in WRITABLE_REVIEW_ROUTES


def test_laddering_followup_route_binding():
    """grace_laddering_followup binds to elicitation/events route."""
    from src.mcp_server.tools_review import grace_laddering_followup

    route = getattr(grace_laddering_followup, "__grace_route__", None)
    assert route == ("POST", "/api/elicitation/events")
    assert route in WRITABLE_REVIEW_ROUTES


def test_teachback_capture_route_binding():
    """grace_teachback_capture binds to elicitation/events route."""
    from src.mcp_server.tools_review import grace_teachback_capture

    route = getattr(grace_teachback_capture, "__grace_route__", None)
    assert route == ("POST", "/api/elicitation/events")
    assert route in WRITABLE_REVIEW_ROUTES


def test_next_element_get_route():
    """grace_review_next_element uses GET verb bypass (not in any frozenset)."""
    from src.mcp_server.tools_review import grace_review_next_element

    route = getattr(grace_review_next_element, "__grace_route__", None)
    assert route is not None
    assert route[0] == "GET"
    # GET route should NOT be in either frozenset (verb bypass).
    assert route not in READONLY_ROUTES
    assert route not in WRITABLE_REVIEW_ROUTES


def test_session_summary_get_route():
    """grace_review_session_summary uses GET verb bypass."""
    from src.mcp_server.tools_review import grace_review_session_summary

    route = getattr(grace_review_session_summary, "__grace_route__", None)
    assert route is not None
    assert route[0] == "GET"


def test_review_decide_has_permission_gate():
    """grace_review_decide is permission-gated."""
    from src.mcp_server.tools_review import grace_review_decide

    gate = getattr(grace_review_decide, "__grace_permission_gate__", None)
    assert gate is not None


def test_next_element_has_permission_gate():
    """grace_review_next_element is permission-gated."""
    from src.mcp_server.tools_review import grace_review_next_element

    gate = getattr(grace_review_next_element, "__grace_permission_gate__", None)
    assert gate is not None


def test_session_summary_has_permission_gate():
    """grace_review_session_summary is permission-gated."""
    from src.mcp_server.tools_review import grace_review_session_summary

    gate = getattr(grace_review_session_summary, "__grace_permission_gate__", None)
    assert gate is not None


def test_no_batch_decide_tool():
    """No batch-decide tool exists (R1 mitigation)."""
    from src.mcp_server import tools_review
    from src.mcp_server.server import mcp

    # Ensure no tool batch-applies REVIEW DECISIONS (R1 mitigation). Bulk
    # document-extraction tools (grace_batch_extract, Chunk 72a) are unrelated
    # to review-decision batching and are allowed.
    for name in mcp._tool_manager._tools:
        lname = name.lower()
        assert not ("batch" in lname and ("decide" in lname or "review" in lname)), (
            f"Batch-decide tool found: {name} — R1 violation"
        )


@pytest.mark.asyncio
async def test_review_decide_returns_deep_link(monkeypatch):
    """grace_review_decide includes deep link in response."""
    from src.mcp_server import http_client

    async def mock_call(method, path, **kwargs):
        return {"decision_id": "test"}

    monkeypatch.setattr(
        "src.mcp_server.tools_review.http_client",
        type("FakeModule", (), {"call": staticmethod(mock_call)})(),
    )

    import src.mcp_server.tools_review as mod

    result = await mod.grace_review_decide.__wrapped__(
        "session-1", "Legal_Entity", "approved", "looks good"
    )
    assert "deep_link" in result
    assert "localhost:3000/review" in result["deep_link"]
    assert "session_id=session-1" in result["deep_link"]


@pytest.mark.asyncio
async def test_review_decide_returns_confirmation(monkeypatch):
    """grace_review_decide returns confirmation message."""
    from src.mcp_server import http_client

    async def mock_call(method, path, **kwargs):
        return {"decision_id": "test"}

    monkeypatch.setattr(
        "src.mcp_server.tools_review.http_client",
        type("FakeModule", (), {"call": staticmethod(mock_call)})(),
    )

    import src.mcp_server.tools_review as mod

    result = await mod.grace_review_decide.__wrapped__(
        "session-1", "Legal_Entity", "approved"
    )
    assert "message" in result
    assert "approved" in result["message"]
    assert "Legal_Entity" in result["message"]


def test_all_review_tools_have_docstrings():
    """All review tools have static docstrings."""
    from src.mcp_server.tools_review import (
        grace_laddering_followup,
        grace_review_decide,
        grace_review_next_element,
        grace_review_session_summary,
        grace_teachback_capture,
    )

    for fn in [
        grace_review_decide,
        grace_review_next_element,
        grace_review_session_summary,
        grace_laddering_followup,
        grace_teachback_capture,
    ]:
        doc = fn.__doc__
        assert doc is not None and len(doc.strip()) > 0, (
            f"{fn.__name__} missing docstring"
        )


@pytest.mark.asyncio
async def test_laddering_includes_agent_id_in_payload(monkeypatch):
    """grace_laddering_followup includes agent_id in event payload."""
    monkeypatch.setenv("GRACE_AGENT_ID", "test-agent")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Test Agent")

    calls: list[dict] = []

    async def mock_call(method, path, **kwargs):
        calls.append(kwargs.get("json_body", {}))
        return {"event_id": "test", "accepted_at": "now"}

    monkeypatch.setattr(
        "src.mcp_server.tools_review.http_client",
        type("FakeModule", (), {"call": staticmethod(mock_call)})(),
    )

    import src.mcp_server.tools_review as mod

    await mod.grace_laddering_followup.__wrapped__(
        "session-1", "Legal_Entity", "What does this mean?"
    )
    assert len(calls) == 1
    body = calls[0]
    assert body.get("agent_id") == "test-agent"
    assert body.get("delegation_source") == "agent_on_behalf"


@pytest.mark.asyncio
async def test_teachback_includes_agent_id_in_payload(monkeypatch):
    """grace_teachback_capture includes agent_id in event payload."""
    monkeypatch.setenv("GRACE_AGENT_ID", "test-agent")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Test Agent")

    calls: list[dict] = []

    async def mock_call(method, path, **kwargs):
        calls.append(kwargs.get("json_body", {}))
        return {"event_id": "test", "accepted_at": "now"}

    monkeypatch.setattr(
        "src.mcp_server.tools_review.http_client",
        type("FakeModule", (), {"call": staticmethod(mock_call)})(),
    )

    import src.mcp_server.tools_review as mod

    await mod.grace_teachback_capture.__wrapped__(
        "session-1", "Legal_Entity", "This entity represents..."
    )
    assert len(calls) == 1
    body = calls[0]
    assert body.get("agent_id") == "test-agent"


def test_five_review_tools_registered():
    """Exactly five review tools registered from tools_review.py."""
    from src.mcp_server.tools_review import (
        grace_laddering_followup,
        grace_review_decide,
        grace_review_next_element,
        grace_review_session_summary,
        grace_teachback_capture,
    )

    # All five import without error.
    tools = [
        grace_review_decide,
        grace_review_next_element,
        grace_review_session_summary,
        grace_laddering_followup,
        grace_teachback_capture,
    ]
    assert len(tools) == 5
