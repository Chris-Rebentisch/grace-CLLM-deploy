"""Spec §11.2 item 7 — happy-path test for all four graph tools.

Covers:

* ``grace_get_entity`` (both modes + both two-mode errors)
* ``grace_get_relationship``
* ``grace_graph_health``
* ``grace_graph_info``
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mcp_server.conftest import build_async_client


@pytest.mark.asyncio
async def test_graph_tools_happy_path(loopback_dns):
    from src.mcp_server.tools_graph import (
        grace_get_entity,
        grace_get_relationship,
        grace_graph_health,
        grace_graph_info,
    )

    # --- grace_get_entity by grace_id -----
    entity_response = {"grace_id": "abc", "entity_type": "Document"}
    client = build_async_client(status_code=200, json_body=entity_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_get_entity(grace_id="abc")
    assert result == entity_response
    call = client.request.call_args
    assert call.args == ("GET", "/api/graph/entities/abc")

    # --- grace_get_entity by (type, name) -----
    lookup_response = {"grace_id": "xyz", "entity": {"name": "X"}}
    client2 = build_async_client(status_code=200, json_body=lookup_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client2
    ):
        result = await grace_get_entity(type="Document", name="X")
    assert result == lookup_response
    call = client2.request.call_args
    assert call.args == ("GET", "/api/graph/entities/lookup")
    params = call.kwargs.get("params")
    assert params == {"type": "Document", "name": "X"}

    # --- two-mode SEMANTIC_INVALID_PARAMS: neither set -----
    envelope = await grace_get_entity()
    assert envelope["code"] == "SEMANTIC_INVALID_PARAMS"
    assert envelope["tool"] == "grace_get_entity"
    assert envelope["status"] is None

    # --- two-mode SEMANTIC_INVALID_PARAMS: both set -----
    envelope2 = await grace_get_entity(
        grace_id="abc", type="Document", name="X"
    )
    assert envelope2["code"] == "SEMANTIC_INVALID_PARAMS"

    # --- grace_get_relationship -----
    rel_response = {"grace_id": "rel-123", "predicate": "relates_to"}
    client3 = build_async_client(status_code=200, json_body=rel_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client3
    ):
        result = await grace_get_relationship(grace_id="rel-123")
    assert result == rel_response
    call = client3.request.call_args
    assert call.args == ("GET", "/api/graph/relationships/rel-123")

    # --- grace_graph_health -----
    health_response = {"status": "ok", "server": {"name": "ArcadeDB"}}
    client4 = build_async_client(status_code=200, json_body=health_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client4
    ):
        result = await grace_graph_health()
    assert result == health_response
    call = client4.request.call_args
    assert call.args == ("GET", "/api/graph/health")

    # --- grace_graph_info -----
    info_response = {"server": {"version": "23.0.1"}}
    client5 = build_async_client(status_code=200, json_body=info_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client5
    ):
        result = await grace_graph_info()
    assert result == info_response
    call = client5.request.call_args
    assert call.args == ("GET", "/api/graph/info")
