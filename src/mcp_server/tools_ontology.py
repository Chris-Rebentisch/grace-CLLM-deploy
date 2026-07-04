"""Ontology tools: active schema, module schema, version history."""

from __future__ import annotations

from src.mcp_server import http_client
from src.mcp_server.server import readonly_tool


@readonly_tool("GET", "/api/ontology/active")
async def grace_get_active_schema() -> dict:
    """Return the currently active GrACE ontology version in full —
    the schema JSON, module breakdown, version metadata, and hash-
    chain fields. Use when you need to know which entity and
    relationship types the graph currently recognises."""
    return await http_client.call(
        "GET",
        "/api/ontology/active",
        tool="grace_get_active_schema",
    )


@readonly_tool("GET", "/api/ontology/modules/{module_name}")
async def grace_get_module_schema(
    module_name: str,
    version_id: str | None = None,
) -> dict:
    """Return the schema for one ontology module. Defaults to the
    active version; pass `version_id` to fetch a historical
    version. Use when you only need one module's entity/property
    shape and do not want to page through the full ontology."""
    return await http_client.call(
        "GET",
        "/api/ontology/modules/{module_name}",
        tool="grace_get_module_schema",
        path_params={"module_name": module_name},
        query_params={"version_id": version_id},
    )


@readonly_tool("GET", "/api/ontology/versions")
async def grace_list_schema_versions(
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List ontology version summaries newest-first, with optional
    paging via `limit` and `offset`. Use to audit how the schema
    has evolved over time or to find a `version_id` to feed into
    `grace_get_module_schema`."""
    return await http_client.call(
        "GET",
        "/api/ontology/versions",
        tool="grace_list_schema_versions",
        query_params={"limit": limit, "offset": offset},
    )
