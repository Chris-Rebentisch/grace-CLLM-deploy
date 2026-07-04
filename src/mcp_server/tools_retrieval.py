"""Retrieval tools: ``grace_search`` and ``grace_answer``.

Both map to approved POST routes that are semantically read-only
queries with request bodies (FastAPI convention, spec §4.4).
"""

from __future__ import annotations

from typing import Literal

from src.mcp_server import http_client
from src.mcp_server.models import (
    RegenerationRequestBody,
    RetrievalRequestBody,
)
from src.mcp_server.server import readonly_tool


PhaseStateArg = Literal[
    "prepare", "open", "structure", "clarify", "close", "none"
]


@readonly_tool("POST", "/api/retrieval/query")
async def grace_search(query: str, limit: int = 10) -> dict:
    """Search the GrACE knowledge graph with a natural-language
    query and return the ranked subgraph — entities, relationships,
    scores, and provenance. Use when you want raw graph context to
    reason over; use `grace_answer` when you want a synthesised
    answer with citations."""
    body = RetrievalRequestBody(query_text=query, top_k=limit)
    return await http_client.call(
        "POST",
        "/api/retrieval/query",
        tool="grace_search",
        json_body=body.model_dump(),
    )


@readonly_tool("POST", "/api/regeneration/query")
async def grace_answer(
    query: str,
    phase_state: PhaseStateArg = "none",
    session_id: str | None = None,
) -> dict:
    """Answer a natural-language question grounded in the GrACE
    graph. Returns a synthesised response with claim spans,
    supporting graph ids, and per-span certainty bands. Pass
    `phase_state` to shape tone for a specific elicitation phase
    (prepare, open, structure, clarify, close). Use this for user-
    facing answers; use `grace_search` for raw candidates."""
    # session_id is telemetry-only — NOT passed to RegenerationQuery
    # (D193 holds; src/regeneration/* untouched).
    if session_id:
        import structlog

        structlog.get_logger().info(
            "grace_answer.session_id",
            session_id=session_id,
        )

    body = RegenerationRequestBody(
        query_text=query, phase_state=phase_state
    )
    return await http_client.call(
        "POST",
        "/api/regeneration/query",
        tool="grace_answer",
        json_body=body.model_dump(),
    )
