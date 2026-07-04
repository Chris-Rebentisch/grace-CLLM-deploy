"""Discovery tools: CQ catalog listing, CQ summary, Ollama health."""

from __future__ import annotations

from src.mcp_server import http_client
from src.mcp_server.server import readonly_tool


@readonly_tool("GET", "/api/discovery/cqs")
async def grace_list_cqs(
    status: str | None = None,
    domain: str | None = None,
    source: str | None = None,
    limit: int | None = None,
) -> dict:
    """List competency questions, optionally filtered by lifecycle
    `status`, ontology `domain`, and/or upstream `source`. Returns
    a list of CQ records. Use to find which questions GrACE has
    been asked to answer and where each one stands."""
    return await http_client.call(
        "GET",
        "/api/discovery/cqs",
        tool="grace_list_cqs",
        query_params={
            "status": status,
            "domain": domain,
            "source": source,
            "limit": limit,
        },
    )


@readonly_tool("GET", "/api/discovery/cqs/summary")
async def grace_cq_summary() -> dict:
    """Return CQ counts bucketed by lifecycle status, domain, and
    source. Use for a quick dashboard view of the competency-
    question catalog rather than paging through the full list."""
    return await http_client.call(
        "GET",
        "/api/discovery/cqs/summary",
        tool="grace_cq_summary",
    )


@readonly_tool("GET", "/api/discovery/ollama-health")
async def grace_ollama_health() -> dict:
    """Check whether the local Ollama inference server is reachable
    and which models it is serving. Returns a status dict. Use as a
    pre-flight before relying on GrACE's local-LLM features."""
    return await http_client.call(
        "GET",
        "/api/discovery/ollama-health",
        tool="grace_ollama_health",
    )
