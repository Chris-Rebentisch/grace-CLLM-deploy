"""D349 (Chunk 43 CP5) — Sensitivity Gate annotation on Query_Event vertices.

Replaces the dropped ``tests/migrations/test_c43b_round_trip.py``: D349
pivot moved Sensitivity Gate audit annotation from a Postgres column to
an additive ArcadeDB ``Query_Event`` property. These tests cover the
contract surface of :func:`persist_query_response` for the new keyword
arguments — vertex-property persistence, encoder behavior, and the
back-compat path where no tags are supplied.

The ArcadeClient is mocked. The contract under test is the OpenCypher
emitted by ``persist_query_response``, not a live ArcadeDB round trip
(mirrors the existing ``test_query_event_writer.py`` discipline).
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.retrieval.query_event_writer import (
    _encode_sensitivity_tags,
    persist_query_response,
)


def _empty_existing() -> dict:
    return {"result": []}


def _create_response() -> dict:
    return {"result": [{"n.grace_id": str(uuid4())}]}


def _ok_metadata() -> dict:
    return {
        "session_id": "sess-1",
        "retrieval_mode": "single_round",
        "strategies_fired": ["semantic"],
        "total_candidates": 1,
        "result_count": 0,
        "serialization_format": "template",
        "latency_ms_total": 12.0,
    }


# ---------- _encode_sensitivity_tags ----------------------------------


def test_encode_sensitivity_tags_returns_none_for_empty_or_missing():
    assert _encode_sensitivity_tags(None) is None
    assert _encode_sensitivity_tags([]) is None
    # All entries dropped → None (not an empty bar string).
    assert _encode_sensitivity_tags(["", "   "]) is None


def test_encode_sensitivity_tags_dedupes_and_canonical_sorts():
    encoded = _encode_sensitivity_tags(["pii", "finance", "pii"])
    assert encoded == "|finance|pii|"


def test_encode_sensitivity_tags_skips_bar_collisions():
    # Tag containing the delimiter is dropped (logged warning), not
    # silently fused into the output.
    encoded = _encode_sensitivity_tags(["pii|inject", "ok"])
    assert encoded == "|ok|"


def test_encode_sensitivity_tags_skips_non_strings_and_whitespace():
    encoded = _encode_sensitivity_tags(
        ["pii", None, 7, "  finance  ", ""]  # type: ignore[list-item]
    )
    assert encoded == "|finance|pii|"


# ---------- persist_query_response — D349 annotation ------------------


@pytest.mark.asyncio
async def test_persist_writes_sensitivity_tags_into_query_event_vertex():
    """The CREATE Query_Event statement carries the bar-delimited form."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing(),     # idempotency probe
        _create_response(),    # CREATE Query_Event
        _create_response(),    # CREATE Response_Event
    ])
    qeid = str(uuid4())
    matrix_id = str(uuid4())

    await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="who owns Acme?",
        results=[],
        response_metadata=_ok_metadata(),
        sensitivity_tags=["pii", "finance_restricted"],
        sensitivity_tags_matrix_id=matrix_id,
    )

    queries = [c.args[0] for c in client.execute_cypher.await_args_list]
    create_q = next(q for q in queries if "CREATE (n:Query_Event" in q)
    # Tags persisted in canonical (sorted) bar-delimited form.
    assert "sensitivity_tags: '|finance_restricted|pii|'" in create_q
    # Matrix UUID persisted alongside.
    assert f"sensitivity_tags_matrix_id: '{matrix_id}'" in create_q


@pytest.mark.asyncio
async def test_persist_omits_sensitivity_property_when_no_tags_supplied():
    """``build_property_map`` skips ``None`` values: the CREATE statement
    must omit ``sensitivity_tags`` when the keyword is not supplied
    (back-compat with pre-D349 callers)."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing(),
        _create_response(),
        _create_response(),
    ])
    qeid = str(uuid4())

    await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=[],
        response_metadata=_ok_metadata(),
        # sensitivity_tags / sensitivity_tags_matrix_id intentionally absent
    )

    queries = [c.args[0] for c in client.execute_cypher.await_args_list]
    create_q = next(q for q in queries if "CREATE (n:Query_Event" in q)
    assert "sensitivity_tags" not in create_q
    assert "sensitivity_tags_matrix_id" not in create_q


@pytest.mark.asyncio
async def test_persist_writes_matrix_id_when_tags_empty_but_matrix_known():
    """A matrix with no tags still records ``sensitivity_tags_matrix_id``
    so the audit trail can distinguish "matrix-without-tags" from
    "annotation never ran" (Q3 SOC 2 fidelity)."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        _empty_existing(),
        _create_response(),
        _create_response(),
    ])
    qeid = str(uuid4())
    matrix_id = str(uuid4())

    await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=[],
        response_metadata=_ok_metadata(),
        sensitivity_tags=[],  # active matrix had no tags
        sensitivity_tags_matrix_id=matrix_id,
    )

    queries = [c.args[0] for c in client.execute_cypher.await_args_list]
    create_q = next(q for q in queries if "CREATE (n:Query_Event" in q)
    # No sensitivity_tags property (None encoded → skipped).
    assert "sensitivity_tags:" not in create_q
    # But matrix UUID IS present.
    assert f"sensitivity_tags_matrix_id: '{matrix_id}'" in create_q


@pytest.mark.asyncio
async def test_persist_idempotent_skip_does_not_re_emit_sensitivity_props():
    """Idempotency short-circuit fires before the CREATE — duplicate
    query_event_id must not double-write the sensitivity annotation."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        # Existence probe finds the vertex → short-circuit return.
        {"result": [{"q.grace_id": str(uuid4())}]},
    ])
    qeid = str(uuid4())

    summary = await persist_query_response(
        client=client,
        query_event_id=qeid,
        query_text="q",
        results=[],
        response_metadata=_ok_metadata(),
        sensitivity_tags=["pii"],
        sensitivity_tags_matrix_id=str(uuid4()),
    )

    assert summary == {
        "query_events_created": 0,
        "response_events_created": 0,
        "edges_created": 0,
    }
    # Only the existence probe was issued; no CREATE statements.
    assert client.execute_cypher.await_count == 1
