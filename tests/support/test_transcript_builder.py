"""Transcript builder tests (Chunk 45, CP6, D374).

Validates content-hash-only policy, three-identity-layer, refused-route
attempts, and summary statistics.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.support.transcript_builder import build_transcript
from src.support.models import TranscriptResponse


@pytest.fixture
def mock_arcade():
    """Mock ArcadeDB client."""
    client = AsyncMock()
    return client


def _make_vertex(
    *,
    query_text: str = "test query",
    path: str = "/api/retrieval/query",
    method: str = "POST",
    status_code: int = 200,
    latency_ms_total: float = 42.5,
    session_id: str | None = "user-123",
    agent_id: str | None = None,
    agent_display_name: str | None = None,
    refused: bool = False,
    graph_scope: str | None = "all",
) -> dict:
    return {
        "query_text": query_text,
        "query_timestamp": datetime.now(UTC).isoformat(),
        "path": path,
        "method": method,
        "status_code": status_code,
        "latency_ms_total": latency_ms_total,
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_display_name": agent_display_name,
        "refused": refused,
        "graph_scope": graph_scope,
    }


@pytest.mark.asyncio
async def test_transcript_includes_all_vertices(mock_arcade):
    """Transcript includes all Query_Event vertices for the session."""
    vertices = [_make_vertex(query_text=f"q{i}") for i in range(3)]
    mock_arcade.execute_cypher.return_value = {"result": vertices}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    assert isinstance(result, TranscriptResponse)
    assert len(result.entries) == 3
    assert result.summary.total_requests == 3


@pytest.mark.asyncio
async def test_transcript_content_hash_not_body(mock_arcade):
    """Entries contain content_hash but never the actual body text."""
    query_text = "sensitive query content"
    vertices = [_make_vertex(query_text=query_text)]
    mock_arcade.execute_cypher.return_value = {"result": vertices}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    entry = result.entries[0]
    expected_hash = hashlib.sha256(query_text.encode()).hexdigest()
    assert entry.content_hash == expected_hash
    # Body text must NOT appear anywhere in the serialized response.
    serialized = result.model_dump_json()
    assert query_text not in serialized


@pytest.mark.asyncio
async def test_three_identity_layer(mock_arcade):
    """Each entry carries three-identity-layer fields."""
    vertices = [_make_vertex(
        session_id="user-abc",
        agent_id="agent-007",
        agent_display_name="Claude",
    )]
    mock_arcade.execute_cypher.return_value = {"result": vertices}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    entry = result.entries[0]
    assert entry.end_user == "user-abc"
    assert entry.agent_id == "agent-007"
    assert entry.agent_display_name == "Claude"
    assert entry.support_operator_email == "op@example.com"


@pytest.mark.asyncio
async def test_refused_route_attempts_included(mock_arcade):
    """Refused-route attempts (403) are included in the transcript."""
    vertices = [
        _make_vertex(status_code=200),
        _make_vertex(status_code=403, refused=True, path="/api/llm/config"),
    ]
    mock_arcade.execute_cypher.return_value = {"result": vertices}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    assert len(result.entries) == 2
    refused_entries = [e for e in result.entries if e.refused]
    assert len(refused_entries) == 1
    assert refused_entries[0].status_code == 403


@pytest.mark.asyncio
async def test_summary_statistics(mock_arcade):
    """Summary includes total, distinct routes, p50/p95."""
    vertices = [
        _make_vertex(latency_ms_total=10.0, path="/api/retrieval/query"),
        _make_vertex(latency_ms_total=20.0, path="/api/retrieval/query"),
        _make_vertex(latency_ms_total=100.0, path="/api/graph/info"),
    ]
    mock_arcade.execute_cypher.return_value = {"result": vertices}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    assert result.summary.total_requests == 3
    assert result.summary.distinct_routes == 2
    assert result.summary.latency_p50_ms == 20.0
    assert result.summary.latency_p95_ms is not None


@pytest.mark.asyncio
async def test_empty_result(mock_arcade):
    """No Query_Event vertices → empty transcript."""
    mock_arcade.execute_cypher.return_value = {"result": []}

    result = await build_transcript(
        session_id=str(uuid4()),
        session_email="op@example.com",
        arcade_client=mock_arcade,
    )

    assert len(result.entries) == 0
    assert result.summary.total_requests == 0
    assert result.summary.distinct_routes == 0
    assert result.summary.latency_p50_ms is None
    assert result.summary.latency_p95_ms is None
