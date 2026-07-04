"""Chunk 39 D306 — MCP change-directive tools delegate to HTTP client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.mcp_server import tools_change_directives


@pytest.mark.asyncio
async def test_grace_list_change_directives_forwards_params():
    with patch(
        "src.mcp_server.tools_change_directives.http_client.call",
        new_callable=AsyncMock,
        return_value={"ok": True},
    ) as call:
        out = await tools_change_directives.grace_list_change_directives(
            status="active",
            tier="Operational_Adjustment",
            authored_by=None,
            limit=50,
        )
    assert out == {"ok": True}
    call.assert_awaited_once()
    kwargs = call.await_args.kwargs
    assert kwargs["query_params"]["status"] == "active"
    assert kwargs["query_params"]["tier"] == "Operational_Adjustment"
    assert kwargs["query_params"]["limit"] == 50


@pytest.mark.asyncio
async def test_grace_get_change_directive_invalid_uuid_raises():
    with pytest.raises(ValueError):
        await tools_change_directives.grace_get_change_directive("not-a-uuid")


@pytest.mark.asyncio
async def test_grace_get_change_directive_calls_http():
    did = str(uuid4())
    with patch(
        "src.mcp_server.tools_change_directives.http_client.call",
        new_callable=AsyncMock,
        return_value={"directive_id": did},
    ) as call:
        out = await tools_change_directives.grace_get_change_directive(did)
    assert out["directive_id"] == did
    call.assert_awaited_once()
    assert call.await_args.kwargs["path_params"]["directive_id"] == did
