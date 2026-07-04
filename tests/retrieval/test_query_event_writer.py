"""Tests for D267 query/response audit-trail writer (Chunk 35b CP2 + CP6).

The ArcadeClient is mocked — the contract under test is the OpenCypher
sequence emitted by ``persist_query_response`` and ``compare_retrieval_sets``,
not a live ArcadeDB round trip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.retrieval.query_event_writer import (
    compare_retrieval_sets,
    persist_query_response,
)


def _make_result(grace_id: str, rerank_score: float = 0.5) -> dict:
    """Plain-dict result item; the writer accepts dicts or Pydantic objects."""
    return {
        "grace_id": grace_id,
        "entity_type": "Legal_Entity",
        "name": f"entity-{grace_id}",
        "rerank_score": rerank_score,
    }


def _empty_existing_response() -> dict:
    """Idempotency probe: simulate a non-existent Query_Event."""
    return {"result": []}


def _existing_query_event_response() -> dict:
    """Idempotency probe: simulate an existing Query_Event vertex."""
    return {"result": [{"q.grace_id": str(uuid4())}]}


def _create_response() -> dict:
    return {"result": [{"n.grace_id": str(uuid4())}]}


@pytest.mark.asyncio
async def test_persist_creates_query_event_vertex():
    """First execute_cypher call probes existence; second creates Query_Event."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing_response(),  # idempotency probe
        _create_response(),           # CREATE Query_Event
        _create_response(),           # CREATE Response_Event
        _create_response(),           # CREATE retrieved_from edge (1 result)
    ])

    qeid = str(uuid4())
    summary = await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="who owns Acme?",
        results=[_make_result("ent-1")],
        response_metadata={
            "session_id": "sess-1",
            "retrieval_mode": "single_round",
            "strategies_fired": ["semantic", "bm25"],
            "total_candidates": 5,
            "result_count": 1,
            "serialization_format": "template",
            "latency_ms_total": 42.0,
        },
    )

    assert summary["query_events_created"] == 1
    # Inspect the second cypher call (CREATE Query_Event)
    call_args = [c.args[0] for c in client.execute_cypher.await_args_list]
    assert any("CREATE (n:Query_Event" in q for q in call_args)
    create_q = next(q for q in call_args if "CREATE (n:Query_Event" in q)
    assert f"query_event_id: '{qeid}'" in create_q
    assert "query_text: 'who owns Acme?'" in create_q
    assert "retrieval_mode: 'single_round'" in create_q


@pytest.mark.asyncio
async def test_persist_creates_response_event_vertex():
    """A Response_Event vertex is created with back-reference to query_event_id."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing_response(),
        _create_response(),
        _create_response(),
    ])

    qeid = str(uuid4())
    summary = await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=[],  # zero results -> zero edges, but Response_Event still created
        response_metadata={
            "retrieval_mode": "iterative_round2",
            "serialization_format": "turtle",
            "latency_ms_total": 100.0,
        },
    )

    assert summary["response_events_created"] == 1
    assert summary["edges_created"] == 0
    call_args = [c.args[0] for c in client.execute_cypher.await_args_list]
    assert any("CREATE (n:Response_Event" in q for q in call_args)
    create_resp_q = next(q for q in call_args if "CREATE (n:Response_Event" in q)
    assert f"query_event_id: '{qeid}'" in create_resp_q
    assert "serialization_format: 'turtle'" in create_resp_q
    assert "latency_ms_total: 100" in create_resp_q


@pytest.mark.asyncio
async def test_persist_creates_one_retrieved_from_edge_per_result():
    """One ``retrieved_from`` edge is created per result, ordered by rank_ordinal."""
    client = AsyncMock()
    # 1 idempotency probe + Query_Event create + Response_Event create + 3 edges
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing_response(),
        _create_response(),
        _create_response(),
        _create_response(),
        _create_response(),
        _create_response(),
    ])

    qeid = str(uuid4())
    results = [_make_result("ent-a"), _make_result("ent-b"), _make_result("ent-c")]
    summary = await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=results,
        response_metadata={"strategies_fired": ["semantic"]},
    )

    assert summary["edges_created"] == 3
    call_args = [c.args[0] for c in client.execute_cypher.await_args_list]
    edge_calls = [q for q in call_args if "[:retrieved_from " in q]
    assert len(edge_calls) == 3
    # Each edge carries rank_ordinal and matching query_event_id
    assert "rank_ordinal: 1" in edge_calls[0]
    assert "rank_ordinal: 2" in edge_calls[1]
    assert "rank_ordinal: 3" in edge_calls[2]
    for q in edge_calls:
        assert f"query_event_id: '{qeid}'" in q


@pytest.mark.asyncio
async def test_persist_idempotent_on_duplicate_query_event_id():
    """If the Query_Event already exists, no further writes happen."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(return_value=_existing_query_event_response())

    qeid = str(uuid4())
    summary = await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=[_make_result("ent-1"), _make_result("ent-2")],
        response_metadata={},
    )

    assert summary == {
        "query_events_created": 0,
        "response_events_created": 0,
        "edges_created": 0,
    }
    # Only the existence probe ran.
    assert client.execute_cypher.await_count == 1


@pytest.mark.asyncio
async def test_persist_propagates_arcade_failure():
    """A raised exception from execute_cypher is propagated (caller handles)."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing_response(),
        RuntimeError("arcade boom"),
    ])

    with pytest.raises(RuntimeError, match="arcade boom"):
        await persist_query_response(
            client=client,
            query_event_id=str(uuid4()),
            query_text="q",
            results=[_make_result("ent-1")],
            response_metadata={},
        )


# --------------------------------------------------------------------------- #
# CP6 — replay diff
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compare_retrieval_sets_returns_added_removed_unchanged_diff():
    """Two query events with overlapping retrieved sets yield correct diff buckets."""
    client = AsyncMock()
    # First call: query A returns ent-1, ent-2, ent-3
    # Second call: query B returns ent-2, ent-3, ent-4 (added=4, removed=1, unchanged=2,3)
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [
            {"grace_id": "ent-1", "rank_ordinal": 1},
            {"grace_id": "ent-2", "rank_ordinal": 2},
            {"grace_id": "ent-3", "rank_ordinal": 3},
        ]},
        {"result": [
            {"grace_id": "ent-2", "rank_ordinal": 1},
            {"grace_id": "ent-3", "rank_ordinal": 2},
            {"grace_id": "ent-4", "rank_ordinal": 3},
        ]},
    ])

    diff = await compare_retrieval_sets(
        client,
        query_event_id_a="qa",
        query_event_id_b="qb",
    )
    assert diff == {
        "added": ["ent-4"],
        "removed": ["ent-1"],
        "unchanged": ["ent-2", "ent-3"],
    }
