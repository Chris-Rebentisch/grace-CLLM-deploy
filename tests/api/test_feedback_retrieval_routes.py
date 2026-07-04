"""Tests for the Chunk 35a retrieval feedback API surface (D266).

Six tests:

1. Happy path — vote=up, no freetext.
2. Happy path — vote=down with freetext, persisted to ``retrieval_feedback``.
3. Vote validation — non-up/down vote returns 422.
4. Freetext length — exceeds 2048 chars returns 422.
5. Append-only — two requests with the same ``query_event_id`` produce
   two distinct rows (no idempotency by design).
6. Mutating-route auth posture — when ``GRACE_ADMIN_KEY`` is set,
   missing ``X-Admin-Key`` header returns 401 even from loopback.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.shared.database import get_engine


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_retrieval_feedback():
    """Wipe ``retrieval_feedback`` around each test for stable counts."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM retrieval_feedback"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM retrieval_feedback"))
        conn.commit()


def _row_count() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM retrieval_feedback")).scalar()
    return int(result or 0)


def test_feedback_up_no_freetext_persists_row(client):
    resp = client.post(
        "/api/feedback/retrieval",
        json={"query_event_id": "qe-001", "vote": "up"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["query_event_id"] == "qe-001"
    assert body["vote"] == "up"
    assert "feedback_id" in body and body["feedback_id"]
    assert "submitted_at" in body
    assert _row_count() == 1


def test_feedback_down_with_freetext_persists_row(client):
    freetext = "Result B was about the wrong fund."
    resp = client.post(
        "/api/feedback/retrieval",
        json={
            "query_event_id": "qe-002",
            "vote": "down",
            "freetext": freetext,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["vote"] == "down"

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT vote, freetext, query_event_id "
                "FROM retrieval_feedback WHERE id = :fid"
            ),
            {"fid": body["feedback_id"]},
        ).first()
    assert row is not None
    assert row.vote == "down"
    assert row.freetext == freetext
    assert row.query_event_id == "qe-002"


def test_feedback_invalid_vote_returns_422(client):
    resp = client.post(
        "/api/feedback/retrieval",
        json={"query_event_id": "qe-003", "vote": "maybe"},
    )
    assert resp.status_code == 422
    assert _row_count() == 0


def test_feedback_freetext_too_long_returns_422(client):
    big = "x" * 2049
    resp = client.post(
        "/api/feedback/retrieval",
        json={"query_event_id": "qe-004", "vote": "up", "freetext": big},
    )
    assert resp.status_code == 422
    assert _row_count() == 0


def test_feedback_append_only_same_query_event_id(client):
    payload = {"query_event_id": "qe-005", "vote": "up"}
    r1 = client.post("/api/feedback/retrieval", json=payload)
    r2 = client.post("/api/feedback/retrieval", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["feedback_id"] != r2.json()["feedback_id"]
    assert _row_count() == 2


def test_feedback_keyed_admission_requires_admin_key(monkeypatch):
    """When ``GRACE_ADMIN_KEY`` is set, a request without the header is 401.

    Mirrors the posture asserted for the Chunk 34 extraction routes
    (``test_extraction_routes.py::test_mine_sample_keyed_admission_requires_admin_key``).
    """
    # Patch the module-level GRACE_ADMIN_KEY directly (read at request time) —
    # no del+reimport of src.api.main/auth_middleware, which would diverge
    # sys.modules from other files' collection-time app refs (the leak that
    # previously broke proposal_create's admin-key test in the full suite).
    monkeypatch.setattr("src.api.auth_middleware.GRACE_ADMIN_KEY", "key-for-test")
    resp = TestClient(app).post(
        "/api/feedback/retrieval",
        json={"query_event_id": "qe-006", "vote": "up"},
    )
    assert resp.status_code == 401, resp.text
