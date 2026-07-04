"""Tests for kill_switch_history table behavior (CP4, D447).

Verifies engage writes history row with previous_state, disengage restores
per-tier state (not blanket-enable), partial unique index rejects double-engage,
and session_id pairing between engage/disengage elicitation events.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.ontology.database import KillSwitchHistoryRow, TrustScoreRow
from src.shared.database import get_session_factory


def _cleanup_history():
    """Remove any leftover kill_switch_history rows via alembic.downgrading GUC."""
    factory = get_session_factory()
    db = factory()
    try:
        db.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        db.execute(text("DELETE FROM kill_switch_history"))
        db.commit()
    finally:
        db.close()


def _ensure_tiers_enabled():
    """Ensure all three tiers have autonomy_enabled=true."""
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
    """Reset kill_switch_history and trust_scores before each test."""
    _cleanup_history()
    _ensure_tiers_enabled()
    yield
    _cleanup_history()
    _ensure_tiers_enabled()


def _engage(client, reason="test engage for history"):
    return client.patch(
        "/api/ontology/daemon/kill-switch",
        json={"autonomy_enabled": False, "reason": reason},
    )


def _disengage(client, reason="test disengage for history"):
    return client.patch(
        "/api/ontology/daemon/kill-switch",
        json={"autonomy_enabled": True, "reason": reason},
    )


class TestKillSwitchHistory:
    """Tests for the kill_switch_history table and per-tier state snapshots."""

    def test_engage_writes_history_row_with_previous_state(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)

        # Get pre-engage state
        status = client.get("/api/ontology/daemon/status").json()
        expected_state = {str(t["tier"]): t["autonomy_enabled"] for t in status["tiers"]}

        resp = _engage(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["previous_state"] == expected_state
        assert data["history_id"] is not None

    def test_disengage_restores_per_tier_state(self):
        """Disengage restores each tier to its pre-engage autonomy_enabled value,
        NOT blanket-enable (D447)."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)

        # Set tier 2 to disabled before engage
        factory = get_session_factory()
        db = factory()
        try:
            tier2 = db.query(TrustScoreRow).filter_by(tier=2).first()
            if tier2:
                tier2.autonomy_enabled = False
                db.commit()
        finally:
            db.close()

        # Engage
        resp_engage = _engage(client)
        assert resp_engage.status_code == 200
        previous = resp_engage.json()["previous_state"]
        assert previous["2"] is False  # tier 2 was disabled

        # Disengage — should restore tier 2 to disabled, not blanket-enable
        resp_disengage = _disengage(client)
        assert resp_disengage.status_code == 200
        restored = resp_disengage.json()["restored_state"]
        assert restored["2"] is False  # tier 2 stays disabled

        # Verify from status endpoint
        status = client.get("/api/ontology/daemon/status").json()
        tier2_status = next(t for t in status["tiers"] if t["tier"] == 2)
        assert tier2_status["autonomy_enabled"] is False

    def test_partial_unique_index_rejects_double_engage(self):
        """Second engage without disengage returns error (partial unique index)."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)

        # First engage
        resp1 = _engage(client, reason="first engage for double test")
        assert resp1.status_code == 200

        # Second engage should fail due to partial unique index — 409
        resp2 = _engage(client, reason="second engage should fail here")
        assert resp2.status_code == 409

    def test_session_id_pairing(self):
        """Engage and disengage elicitation events share session_id = kill_switch_history.id."""
        from fastapi.testclient import TestClient
        from src.api.main import app
        import src.api.daemon_routes as dr_module

        client = TestClient(app)

        captured_events = []
        original_enqueue = dr_module.enqueue_event

        def capturing_enqueue(**kwargs):
            captured_events.append(kwargs)
            return original_enqueue(**kwargs)

        with patch.object(dr_module, "enqueue_event", side_effect=capturing_enqueue):
            resp_engage = _engage(client, reason="session pairing test engage")
            assert resp_engage.status_code == 200
            history_id = resp_engage.json()["history_id"]

            resp_disengage = _disengage(client, reason="session pairing test disengage")
            assert resp_disengage.status_code == 200

        # Both events should share the same session_id_override = history_id
        assert len(captured_events) >= 2
        engage_event = captured_events[0]
        disengage_event = captured_events[1]
        assert str(engage_event["session_id_override"]) == history_id
        assert str(disengage_event["session_id_override"]) == history_id
