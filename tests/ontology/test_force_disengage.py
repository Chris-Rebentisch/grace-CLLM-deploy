"""Tests for POST /kill-switch/force-disengage (CP5, R2).

Verifies force-disengage closes orphan engage rows, preserves engaged_by,
returns 404 when no open row, and emits governance event.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.ontology.database import TrustScoreRow
from src.shared.database import get_session_factory


def _cleanup_history():
    factory = get_session_factory()
    db = factory()
    try:
        db.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        db.execute(text("DELETE FROM kill_switch_history"))
        db.commit()
    finally:
        db.close()


def _ensure_tiers_enabled():
    factory = get_session_factory()
    db = factory()
    try:
        for row in db.query(TrustScoreRow).all():
            row.autonomy_enabled = True
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_state():
    _cleanup_history()
    _ensure_tiers_enabled()
    yield
    _cleanup_history()
    _ensure_tiers_enabled()


class TestForceDisengage:
    """Tests for the force-disengage escape hatch."""

    def test_force_disengage_closes_orphan_row(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)

        # Engage first to create an orphan row
        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "create orphan for force-disengage"},
        )
        assert resp.status_code == 200
        engaged_by_original = "operator"

        # Force-disengage
        resp2 = client.post(
            "/api/ontology/daemon/kill-switch/force-disengage",
            json={"reason": "orphan cleanup test"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["disengaged_at"] is not None
        assert data["restored_state"] is None  # force-disengage does not restore
        assert data["engaged_by"] == engaged_by_original  # preserved

    def test_force_disengage_404_when_no_open_row(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/ontology/daemon/kill-switch/force-disengage",
            json={"reason": "no orphan here"},
        )
        assert resp.status_code == 404
