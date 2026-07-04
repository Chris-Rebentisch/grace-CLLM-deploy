"""F-0046 / ISS-0048 — grace_session_start must surface the real session id.

POST /api/ontology/review/start returns the ReviewSession model, whose
primary-key field is ``id`` (there is no ``session_id`` key). The MCP tool
previously read only "session_id", so every start reported "unknown" and
grace_session_close then 422'd parsing "unknown" as a UUID.

Mocked http_client — no live FastAPI, no DB.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


def _fake_http_module(response: dict, calls: list | None = None):
    async def mock_call(method, path, **kwargs):
        if calls is not None:
            calls.append((method, path, kwargs.get("json_body")))
        return response

    return type("FakeModule", (), {"call": staticmethod(mock_call)})()


@pytest.mark.asyncio
async def test_session_start_reads_id_key_from_review_start_response(monkeypatch):
    """The real route returns ``id`` — the tool must surface it as session_id."""
    real_id = str(uuid4())
    # Shape mirrors ReviewSession.model_dump(mode="json") — key is "id".
    response = {"id": real_id, "status": "in_progress", "reviewer": "mcp-user"}
    monkeypatch.setattr(
        "src.mcp_server.tools_session.http_client",
        _fake_http_module(response),
    )

    import src.mcp_server.tools_session as mod

    result = await mod.grace_session_start.__wrapped__("prepare")
    assert result["session_id"] == real_id
    assert real_id in result["message"]
    assert result["session_id"] != "unknown"


@pytest.mark.asyncio
async def test_session_start_still_accepts_legacy_session_id_key(monkeypatch):
    """Forward-compat: a response carrying session_id keeps working."""
    monkeypatch.setattr(
        "src.mcp_server.tools_session.http_client",
        _fake_http_module({"session_id": "abc-123"}),
    )

    import src.mcp_server.tools_session as mod

    result = await mod.grace_session_start.__wrapped__("prepare")
    assert result["session_id"] == "abc-123"


@pytest.mark.asyncio
async def test_session_start_falls_back_to_unknown_when_no_id(monkeypatch):
    monkeypatch.setattr(
        "src.mcp_server.tools_session.http_client",
        _fake_http_module({"status": "in_progress"}),
    )

    import src.mcp_server.tools_session as mod

    result = await mod.grace_session_start.__wrapped__("prepare")
    assert result["session_id"] == "unknown"


@pytest.mark.asyncio
async def test_start_then_close_flow_uses_real_uuid(monkeypatch):
    """End-to-end shape: the id from start drives close's request bodies."""
    real_id = str(uuid4())
    calls: list = []

    async def mock_call(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        if path == "/api/ontology/review/start":
            return {"id": real_id, "status": "in_progress"}
        return {"ok": True}

    fake = type("FakeModule", (), {"call": staticmethod(mock_call)})()
    monkeypatch.setattr("src.mcp_server.tools_session.http_client", fake)

    import src.mcp_server.tools_session as mod

    start_result = await mod.grace_session_start.__wrapped__("prepare")
    close_result = await mod.grace_session_close.__wrapped__(
        start_result["session_id"]
    )

    close_bodies = [body for (_, path, body) in calls if "close" in path]
    assert len(close_bodies) == 2
    for body in close_bodies:
        assert body["session_id"] == real_id
    assert close_result["session_id"] == real_id
