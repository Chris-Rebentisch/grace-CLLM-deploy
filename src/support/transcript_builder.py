"""Audit-export transcript builder for support sessions (D374, Chunk 45 CP6).

Queries ``Query_Event`` vertices from ArcadeDB by ``support_session_id``,
builds content-hash-only transcript entries (never includes request/response
bodies), and returns a ``TranscriptResponse`` with three-identity-layer
per entry and summary statistics.

Public API
----------
* :func:`build_transcript` — async, takes session_id + session_email +
  arcade_client, returns ``TranscriptResponse``.
"""

from __future__ import annotations

import hashlib
import statistics
from uuid import UUID

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.support.models import (
    TranscriptEntry,
    TranscriptResponse,
    TranscriptSummary,
)

log = structlog.get_logger()


async def build_transcript(
    *,
    session_id: str,
    session_email: str,
    arcade_client: ArcadeClient,
) -> TranscriptResponse:
    """Build an audit-export transcript for a support session.

    Args:
        session_id: UUID string of the support session.
        session_email: email of the support operator (from session record).
        arcade_client: live ArcadeDB HTTP client.

    Returns:
        TranscriptResponse with content-hash-only entries, three-identity-layer,
        refused-route attempts, and summary statistics.
    """
    escaped_sid = escape_cypher_string(session_id)
    query = (
        f"MATCH (q:Query_Event) "
        f"WHERE q.support_session_id = '{escaped_sid}' "
        f"RETURN q ORDER BY q.query_timestamp"
    )

    try:
        result = await arcade_client.execute_cypher(query)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "transcript_builder.query_failed",
            session_id=session_id,
            error=str(exc),
        )
        result = {"result": []}

    rows = result.get("result", []) or []

    entries: list[TranscriptEntry] = []
    latencies: list[float] = []

    for row in rows:
        vertex = row if isinstance(row, dict) else {}
        # ArcadeDB may nest the vertex under a key like "q".
        if "q" in vertex and isinstance(vertex["q"], dict):
            vertex = vertex["q"]

        query_text = vertex.get("query_text", "")
        # D374: content hash of the query text (never the body itself).
        content_hash = hashlib.sha256(
            query_text.encode() if isinstance(query_text, str) else b""
        ).hexdigest()

        latency_val = vertex.get("latency_ms_total")
        latency_ms = float(latency_val) if latency_val is not None else None
        if latency_ms is not None:
            latencies.append(latency_ms)

        entry = TranscriptEntry(
            timestamp=vertex.get("query_timestamp", "1970-01-01T00:00:00Z"),
            path=vertex.get("path", "/api/retrieval/query"),
            method=vertex.get("method", "POST"),
            status_code=int(vertex.get("status_code", 200)),
            content_hash=content_hash,
            latency_ms=latency_ms,
            graph_scope=vertex.get("graph_scope"),
            # Three-identity-layer (D374).
            end_user=vertex.get("session_id"),
            agent_id=vertex.get("agent_id"),
            agent_display_name=vertex.get("agent_display_name"),
            support_operator_email=session_email,
            refused=bool(vertex.get("refused", False)),
        )
        entries.append(entry)

    # Distinct routes.
    distinct_routes = len({e.path for e in entries})

    # Latency percentiles.
    p50: float | None = None
    p95: float | None = None
    if latencies:
        sorted_lat = sorted(latencies)
        p50 = float(statistics.median(sorted_lat))
        idx_95 = int(len(sorted_lat) * 0.95)
        p95 = float(sorted_lat[min(idx_95, len(sorted_lat) - 1)])

    summary = TranscriptSummary(
        total_requests=len(entries),
        distinct_routes=distinct_routes,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
    )

    return TranscriptResponse(
        session_id=UUID(session_id),
        entries=entries,
        summary=summary,
    )
