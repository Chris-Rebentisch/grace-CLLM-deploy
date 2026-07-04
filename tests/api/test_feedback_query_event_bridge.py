"""Integration bridge: D267 query_event_id flows from writer to feedback row.

Chunk 35b CP8 — verifies that the existing
``POST /api/feedback/retrieval`` route accepts ``query_event_id`` in two
shapes:

1. Without a writer-persisted upstream event — the feedback row is still
   accepted (correlation by value, no FK to graph). This guards the
   mixed-mode coexistence policy (35a feedback rows do not require
   35b writer success).
2. With an id that matches a value previously threaded through
   ``persist_query_response`` — the feedback row carries the same id.
   This is the forward-correlation contract.

The ArcadeClient is mocked because the test suite runs without a live
ArcadeDB; the bridge under test is the API contract, not the graph
write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.retrieval.query_event_writer import persist_query_response
from src.shared.database import get_engine


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_retrieval_feedback():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM retrieval_feedback"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM retrieval_feedback"))
        conn.commit()


def test_feedback_without_prior_query_event_still_accepted(client):
    """A feedback row with a free-form query_event_id is accepted (35a contract).

    The 35b writer is not run; this guards mixed-mode clients where the
    server-side query_event_id surfacing has not yet been adopted by the
    caller.
    """
    resp = client.post(
        "/api/feedback/retrieval",
        json={"query_event_id": "qe-no-prior-event", "vote": "up"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["query_event_id"] == "qe-no-prior-event"
    assert body["vote"] == "up"


@pytest.mark.asyncio
async def test_feedback_with_id_from_writer_carries_id():
    """An id threaded through `persist_query_response` round-trips on feedback."""
    qeid = str(uuid4())

    # Drive the writer with a mocked Arcade client so the test doesn't
    # require a live graph; the writer's contract is verified separately
    # in tests/retrieval/test_query_event_writer.py.
    fake_arcade = AsyncMock()
    fake_arcade.execute_cypher = AsyncMock(side_effect=[
        {"result": []},  # idempotency probe
        {"result": [{"n.grace_id": "q-grace"}]},  # CREATE Query_Event
        {"result": [{"n.grace_id": "r-grace"}]},  # CREATE Response_Event
    ])
    summary = await persist_query_response(
        client=fake_arcade,
        query_event_id=qeid,
        query_text="bridge-test",
        results=[],
        response_metadata={"strategies_fired": ["semantic"]},
    )
    assert summary["query_events_created"] == 1

    # Now post feedback with that same id.
    test_client = TestClient(app)
    resp = test_client.post(
        "/api/feedback/retrieval",
        json={"query_event_id": qeid, "vote": "up"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["query_event_id"] == qeid

    # Verify it landed in retrieval_feedback with the same id (correlation
    # by value, no FK).
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT query_event_id FROM retrieval_feedback "
                "WHERE id = :fid"
            ),
            {"fid": body["feedback_id"]},
        ).first()
    assert row is not None
    assert row.query_event_id == qeid
