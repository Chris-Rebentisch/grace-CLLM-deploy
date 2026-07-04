"""Read-only Change Directive tools (Chunk 39, D306)."""

from __future__ import annotations

from uuid import UUID

from src.mcp_server import http_client
from src.mcp_server.server import readonly_tool


@readonly_tool("GET", "/api/change-directives")
async def grace_list_change_directives(
    status: str | None = None,
    tier: str | None = None,
    authored_by: str | None = None,
    limit: int = 25,
) -> dict:
    """List Change Directives visible to the upstream actor (same filters as HTTP)."""
    return await http_client.call(
        "GET",
        "/api/change-directives",
        tool="grace_list_change_directives",
        query_params={
            "status": status,
            "tier": tier,
            "authored_by": authored_by,
            "limit": limit,
        },
    )


@readonly_tool("GET", "/api/change-directives/{directive_id}")
async def grace_get_change_directive(directive_id: str) -> dict:
    """Fetch one Change Directive including ``latest_snapshot`` when present."""
    UUID(directive_id)
    return await http_client.call(
        "GET",
        "/api/change-directives/{directive_id}",
        tool="grace_get_change_directive",
        path_params={"directive_id": directive_id},
    )
