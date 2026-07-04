"""F-027 / ISS-0010 — kill-switch disengage-without-open-row hazard.

Regression tests for the validation-run sequence: engage (per-tier snapshot)
→ force-disengage (closes the row, restores nothing) → normal PATCH
{autonomy_enabled: true} previously blanket-enabled ALL tiers, granting
tiers 2/3 autonomy they never earned. Also covers the status endpoint now
deriving ``kill_switch_engaged`` from the presence of an open engage row
instead of the all-tiers-off heuristic.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.ontology.database import KillSwitchHistoryRow, TrustScoreRow
from src.shared.database import get_session_factory

client = TestClient(app)


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


def _set_tiers(states: dict[int, bool]):
    """Set per-tier autonomy_enabled, creating rows as needed."""
    factory = get_session_factory()
    db = factory()
    try:
        for tier, enabled in states.items():
            row = db.query(TrustScoreRow).filter_by(tier=tier).first()
            if row is None:
                db.add(TrustScoreRow(
                    id=uuid4(), tier=tier, trust_score=0.5,
                    autonomy_threshold=0.95, autonomy_enabled=enabled,
                    window_size=50, min_reviews_for_calibration=50,
                    risk_tolerance=0.95, total_decisions=0,
                    regression_detected=False,
                ))
            else:
                row.autonomy_enabled = enabled
        db.commit()
    finally:
        db.close()


def _tier_states() -> dict[str, bool]:
    factory = get_session_factory()
    db = factory()
    try:
        return {
            str(r.tier): r.autonomy_enabled
            for r in db.query(TrustScoreRow).order_by(TrustScoreRow.tier).all()
        }
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_state():
    _cleanup_history()
    _set_tiers({1: True, 2: False, 3: False})
    yield
    _cleanup_history()
    _set_tiers({1: True, 2: True, 3: True})


class TestDisengageWithoutOpenRow409:
    """Disengage/enable with no open engage row must refuse, not blanket-enable."""

    def test_enable_without_open_row_returns_409(self):
        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "attempt blanket enable"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "No open kill-switch engage row" in detail
        assert "per-tier" in detail

    def test_enable_without_open_row_changes_no_tier_state(self):
        before = _tier_states()
        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "attempt blanket enable"},
        )
        assert resp.status_code == 409
        assert _tier_states() == before  # tiers 2/3 stay off — never blanket-enabled

    def test_enable_without_open_row_writes_no_governance_event(self):
        """The 409 refusal must not silently write a state-change audit event."""
        factory = get_session_factory()
        db = factory()
        try:
            before = db.execute(text(
                "SELECT COUNT(*) FROM governance_decision_events "
                "WHERE decision_type = 'kill_switch_disengaged'"
            )).scalar()
        finally:
            db.close()

        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "attempt blanket enable"},
        )
        assert resp.status_code == 409

        db = factory()
        try:
            after = db.execute(text(
                "SELECT COUNT(*) FROM governance_decision_events "
                "WHERE decision_type = 'kill_switch_disengaged'"
            )).scalar()
        finally:
            db.close()
        assert after == before

    def test_golden_run_sequence_engage_force_disengage_enable(self):
        """The exact F-027 live sequence now ends in 409, not tiers_updated=3."""
        # Engage — snapshot {1:on, 2:off, 3:off}
        resp_engage = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "engage for F-027 sequence"},
        )
        assert resp_engage.status_code == 200
        assert resp_engage.json()["previous_state"] == {"1": True, "2": False, "3": False}

        # Force-disengage — closes the orphan row, restores nothing
        resp_force = client.post(
            "/api/ontology/daemon/kill-switch/force-disengage",
            json={"reason": "F-027 sequence force-disengage"},
        )
        assert resp_force.status_code == 200
        assert resp_force.json()["restored_state"] is None

        # Normal enable — previously blanket-enabled all 3 tiers; now 409
        resp_enable = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "post-force blanket enable"},
        )
        assert resp_enable.status_code == 409
        assert _tier_states() == {"1": False, "2": False, "3": False}

    def test_normal_engage_disengage_restore_still_works(self):
        """D447 snapshot/restore semantics preserved on the normal path."""
        resp_engage = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "normal engage for restore"},
        )
        assert resp_engage.status_code == 200

        resp_disengage = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "normal disengage restores"},
        )
        assert resp_disengage.status_code == 200
        assert resp_disengage.json()["restored_state"] == {"1": True, "2": False, "3": False}
        assert _tier_states() == {"1": True, "2": False, "3": False}


class TestStatusDerivedFromOpenRow:
    """kill_switch_engaged derives from the open engage row, not all-tiers-off."""

    def test_status_not_engaged_when_all_tiers_off_but_no_open_row(self):
        """All tiers off via calibration is NOT an engaged kill switch."""
        _set_tiers({1: False, 2: False, 3: False})
        resp = client.get("/api/ontology/daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kill_switch_engaged"] is False
        assert data["previous_state"] is None

    def test_status_engaged_while_open_row_exists(self):
        resp_engage = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "engage for status probe"},
        )
        assert resp_engage.status_code == 200

        resp = client.get("/api/ontology/daemon/status")
        data = resp.json()
        assert data["kill_switch_engaged"] is True
        assert data["previous_state"] == {"1": True, "2": False, "3": False}

    def test_status_not_engaged_after_force_disengage(self):
        """The F-027(a) half: force-disengage leaves all tiers off, but the
        status endpoint must report disengaged (row is closed)."""
        client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "engage before force probe"},
        )
        resp_force = client.post(
            "/api/ontology/daemon/kill-switch/force-disengage",
            json={"reason": "force for status probe"},
        )
        assert resp_force.status_code == 200

        resp = client.get("/api/ontology/daemon/status")
        data = resp.json()
        assert data["kill_switch_engaged"] is False
        # All tiers are still off (force-disengage restores nothing)…
        assert all(not t["autonomy_enabled"] for t in data["tiers"])
        # …but no snapshot is advertised for restore.
        assert data["previous_state"] is None
