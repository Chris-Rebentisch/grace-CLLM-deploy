"""Tests for the Reconciliation Layer route surface (Chunk 36, D280/D283).

Six tests:

1. Generate-happy: POST against a completed session returns 201 +
   GapReportResponse; ``gap_reports`` row written; ``review_sessions``
   denormalization columns updated.
2. Generate-422: POST against a session whose status is not
   ``"completed"`` returns 422 (D280 lifecycle gating).
3. Regenerate-409: POST without ``?force=true`` against a session that
   already has a report returns 409.
4. Regenerate-201: POST with ``?force=true`` returns 201 (new row
   appended; old row preserved).
5. Force-regen 429: second ``?force=true`` within the 60s window returns
   429 (AC15 / rate limit).
6. Get-happy + 404: GET returns 200 with the most recent report; GET
   for an unknown session returns 404.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.graph.arcade_client import get_arcade_client
from src.shared.database import get_engine


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_arcade_client():
    """Replace the Arcade dependency with an async mock returning a
    populated graph (>= floor of 100 V) so the score code-path runs."""
    fake = AsyncMock()
    fake.execute_sql = AsyncMock(
        return_value={
            "result": [
                {"type_name": "Company", "cnt": 80},
                {"type_name": "Insurance_Policy", "cnt": 30},
            ]
        }
    )
    app.dependency_overrides[get_arcade_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_arcade_client, None)


@pytest.fixture(autouse=True)
def _clean_recon_tables():
    engine = get_engine()
    with engine.connect() as conn:
        # Order matters: review_sessions FK gap_reports; gap_reports FK
        # review_sessions. Break the cycle by clearing the FK column first.
        conn.execute(text("UPDATE review_sessions SET gap_report_id = NULL"))
        conn.execute(text("DELETE FROM gap_reports"))
        conn.execute(text("DELETE FROM review_decisions"))
        conn.execute(text("DELETE FROM review_sessions"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("UPDATE review_sessions SET gap_report_id = NULL"))
        conn.execute(text("DELETE FROM gap_reports"))
        conn.execute(text("DELETE FROM review_decisions"))
        conn.execute(text("DELETE FROM review_sessions"))
        conn.commit()


def _insert_session(status: str = "completed", reviewer: str = "alice") -> UUID:
    """Insert a minimal ``review_sessions`` row and return its id."""
    sid = uuid4()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO review_sessions
                    (id, status, reviewer, seed_schema_merge_run_id,
                     seed_schema_snapshot)
                VALUES
                    (:id, :status, :rev, :merge,
                     CAST('{}' AS JSONB))
                """
            ),
            {
                "id": str(sid),
                "status": status,
                "rev": reviewer,
                "merge": "test-merge-run",
            },
        )
        conn.commit()
    return sid


def _insert_decisions(session_id: UUID, n_strong: int = 3) -> None:
    """Insert ``n_strong`` approved review_decisions whose
    ``metadata_extra.evidence_items_viewed`` length >= 3 (strong)."""
    engine = get_engine()
    with engine.connect() as conn:
        for i in range(n_strong):
            conn.execute(
                text(
                    """
                    INSERT INTO review_decisions
                        (id, session_id, element_type, element_name,
                         decision, original_data, reviewer, metadata_extra)
                    VALUES
                        (:id, :sid, 'entity_type', :name,
                         'approved', CAST('{}' AS JSONB), 'alice',
                         CAST(:meta AS JSONB))
                    """
                ),
                {
                    "id": str(uuid4()),
                    "sid": str(session_id),
                    "name": f"Company_{i}",
                    "meta": '{"evidence_items_viewed": ["e1","e2","e3"]}',
                },
            )
        conn.commit()


def _row_count_gap_reports(session_id: UUID) -> int:
    engine = get_engine()
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM gap_reports WHERE session_id = :sid"),
            {"sid": str(session_id)},
        ).scalar()
    return int(n or 0)


# ---------------------------------------------------------------------------
# 1. Generate-happy.
# ---------------------------------------------------------------------------


def test_generate_happy_path_returns_201_against_completed_session(client):
    sid = _insert_session(status="completed")
    _insert_decisions(sid, n_strong=3)

    resp = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert body["session_id"] == str(sid)
    assert body["evidence_grounding_threshold"] == 3
    # 3 strong-evidence approvals, all >= threshold; score should be 1.0.
    assert body["evidence_grounding_score"] == 1.0
    assert body["graph_population_floor_breach"] is None

    assert _row_count_gap_reports(sid) == 1

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT gap_report_id, erd_score, erd_threshold_n "
                "FROM review_sessions WHERE id = :sid"
            ),
            {"sid": str(sid)},
        ).first()
    assert row is not None
    assert row.gap_report_id is not None
    assert row.erd_score == 1.0
    assert row.erd_threshold_n == 3


# ---------------------------------------------------------------------------
# 2. Generate-422 (D280 lifecycle gating).
# ---------------------------------------------------------------------------


def test_generate_422_when_session_not_completed(client):
    sid = _insert_session(status="in_progress")

    resp = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert resp.status_code == 422, resp.text
    assert _row_count_gap_reports(sid) == 0


# ---------------------------------------------------------------------------
# 3. Regenerate-409 (existing report, no ?force=true).
# ---------------------------------------------------------------------------


def test_regenerate_409_when_report_exists_without_force(client):
    sid = _insert_session(status="completed")
    _insert_decisions(sid, n_strong=3)

    first = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert first.status_code == 201

    second = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert second.status_code == 409
    assert _row_count_gap_reports(sid) == 1


# ---------------------------------------------------------------------------
# 4. Regenerate-201 with ?force=true (new row, old preserved).
# ---------------------------------------------------------------------------


def test_regenerate_201_with_force_true_appends_new_row(client):
    sid = _insert_session(status="completed")
    _insert_decisions(sid, n_strong=3)

    first = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert first.status_code == 201

    second = client.post(
        f"/api/recon/gap-report/{sid}/generate?force=true"
    )
    assert second.status_code == 201, second.text
    assert _row_count_gap_reports(sid) == 2


# ---------------------------------------------------------------------------
# 5. Force-regen 429 (AC15).
# ---------------------------------------------------------------------------


def test_force_regenerate_returns_429_within_window(client):
    """Two ``?force=true`` calls within 60s for the same session → 429."""
    sid = _insert_session(status="completed")
    _insert_decisions(sid, n_strong=3)

    first = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert first.status_code == 201

    second = client.post(
        f"/api/recon/gap-report/{sid}/generate?force=true"
    )
    assert second.status_code == 201, second.text

    third = client.post(
        f"/api/recon/gap-report/{sid}/generate?force=true"
    )
    assert third.status_code == 429, third.text
    detail = third.json().get("detail", "")
    assert "rate limit" in detail.lower()


# ---------------------------------------------------------------------------
# 6. Get-happy + 404.
# ---------------------------------------------------------------------------


def test_get_returns_most_recent_report_and_404_for_unknown(client):
    sid = _insert_session(status="completed")
    _insert_decisions(sid, n_strong=3)

    gen = client.post(f"/api/recon/gap-report/{sid}/generate")
    assert gen.status_code == 201

    got = client.get(f"/api/recon/gap-report/{sid}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["session_id"] == str(sid)
    assert body["evidence_grounding_threshold"] == 3

    unknown = uuid4()
    miss = client.get(f"/api/recon/gap-report/{unknown}")
    assert miss.status_code == 404
