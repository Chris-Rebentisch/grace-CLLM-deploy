"""Spec §11.2 items 11–14 — Layer A / Layer B error boundary tests.

Layer A (SDK-owned, JSON-RPC -32602): tested via ``FastMCP.call_tool``
which raises ``ToolError`` when input fails the registered JSON
Schema. This boundary is enforced by the SDK *before* the tool
function runs; GrACE does not own the error shape.

Layer B (GrACE-owned, ``MCPErrorEnvelope``): everything else —
timeouts, upstream errors, semantic two-mode conflicts, airgap /
read-only violations.

Mixing the two shapes is the #1 regression point the reviewer
flagged across rounds 1–4 (prompt §Ready to Build).
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tests.mcp_server.conftest import build_async_client


@pytest.mark.asyncio
async def test_timeout_returns_structured_error(loopback_dns):
    """Item 11: httpx timeout → ``UPSTREAM_TIMEOUT`` Layer B envelope.
    ``status`` is ``None`` (no HTTP response); ``tool`` is populated."""
    from src.mcp_server.tools_retrieval import grace_search

    client = build_async_client(
        request_exception=httpx.ReadTimeout("boom")
    )
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_search(query="x")
    assert result["code"] == "UPSTREAM_TIMEOUT"
    assert result["status"] is None
    assert result["tool"] == "grace_search"
    assert "message" in result


@pytest.mark.asyncio
async def test_invalid_arg_schema_rejected():
    """Item 12 / spec §7.4 Layer A: the SDK rejects empty args for a
    tool with a required field BEFORE the tool function runs. The
    error is JSON-RPC native (``ToolError`` in-process; ``-32602
    InvalidParams`` on the wire) — NOT an ``MCPErrorEnvelope``."""
    from src.mcp_server import tools_retrieval  # noqa: F401
    from src.mcp_server.server import mcp

    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("grace_search", {})

    # The ToolError is the SDK's Layer A boundary. It is not a Layer
    # B envelope — assert required-field schema validation details and
    # absence of Layer B envelope codes.
    msg = str(exc_info.value)
    assert "validation error" in msg
    assert "Field required" in msg
    assert "grace_searchArguments" in msg
    assert "UPSTREAM_TIMEOUT" not in msg
    assert "UPSTREAM_UNAVAILABLE" not in msg
    assert "SEMANTIC_INVALID_PARAMS" not in msg
    assert "AIRGAP_VIOLATION" not in msg


@pytest.mark.asyncio
async def test_empty_result_normalized(loopback_dns):
    """Item 13: upstream returns 200 with an empty result set. The
    tool returns the dict as-is — no error envelope — so the host
    LLM can render "no results" naturally from the shape."""
    from src.mcp_server.tools_retrieval import grace_search

    empty_response = {
        "query": "nothing",
        "results": [],
        "serialized_context": "",
        "serialization_format": "template",
        "total_candidates": 0,
        "strategy_contributions": {},
        "latency_ms": {},
    }
    client = build_async_client(status_code=200, json_body=empty_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_search(query="nothing")

    # Not an error envelope.
    assert "code" not in result or result.get("code") not in {
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "UPSTREAM_NOT_FOUND",
    }
    assert result == empty_response
    assert result["results"] == []


@pytest.mark.asyncio
async def test_non_json_upstream_returns_upstream_unavailable(
    loopback_dns,
):
    """Item 14: upstream returns 502 with an HTML body (reverse-proxy
    failure pattern). Tool returns Layer B envelope with
    ``UPSTREAM_UNAVAILABLE`` and ``status=502``. The HTML body must
    NOT appear anywhere in the envelope — no leak."""
    from src.mcp_server.tools_graph import grace_graph_health

    # 5xx upstream — http_client maps before attempting to parse JSON.
    client = build_async_client(
        status_code=502,
        json_exception=ValueError("not json"),
    )
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_graph_health()

    assert result["code"] == "UPSTREAM_UNAVAILABLE"
    assert result["status"] == 502
    assert result["tool"] == "grace_graph_health"

    # No HTML leak — the serialized envelope contains no "<html" marker.
    import json

    serialized = json.dumps(result)
    assert "<html" not in serialized
    assert "</html>" not in serialized
