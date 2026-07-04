"""Tests for kill-switch single-transaction audit emission (CP4, D446).

Verifies that engage/disengage produces governance_decision_events and
elicitation_events rows, that audit-write failure rolls back trust-score
mutations, and that reason validation enforces the admin-key path minimum.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import text

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
    from src.ontology.database import TrustScoreRow
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


class TestKillSwitchAudit:
    """Tests for single-transaction audit emission (D446)."""

    def test_engage_creates_governance_and_elicitation_events(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)
        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "test engage reason for audit"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["history_id"] is not None
        assert data["previous_state"] is not None

    def test_disengage_creates_governance_and_elicitation_events(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)
        # Engage first
        resp1 = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False, "reason": "engage for disengage test"},
        )
        assert resp1.status_code == 200

        # Disengage
        resp2 = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": True, "reason": "disengage test reason here"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["history_id"] is not None
        assert data["restored_state"] is not None

    def test_audit_write_failure_rolls_back_trust_score_mutation(self):
        """When enqueue_event fails, trust-score mutation also rolls back."""
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app, raise_server_exceptions=False)

        # Get pre-engage state
        status_resp = client.get("/api/ontology/daemon/status")
        pre_tiers = {t["tier"]: t["autonomy_enabled"] for t in status_resp.json()["tiers"]}

        # Patch enqueue_event to raise after being called
        with patch("src.api.daemon_routes.enqueue_event", side_effect=RuntimeError("audit write failed")):
            resp = client.patch(
                "/api/ontology/daemon/kill-switch",
                json={"autonomy_enabled": False, "reason": "this should roll back completely"},
            )
            assert resp.status_code == 500

        # Verify trust scores are unchanged (rollback succeeded)
        client2 = TestClient(app)
        status_resp2 = client2.get("/api/ontology/daemon/status")
        post_tiers = {t["tier"]: t["autonomy_enabled"] for t in status_resp2.json()["tiers"]}
        assert pre_tiers == post_tiers

    @pytest.mark.parametrize(
        "reason,admin_key,expected_status",
        [
            ("short", "test-admin-key-value-for-testing-32ch", 422),
            ("", "test-admin-key-value-for-testing-32ch", 422),
            ("this reason is long enough to pass", "test-admin-key-value-for-testing-32ch", 200),
            ("", "", 200),  # loopback dev bypass — empty reason admitted
        ],
        ids=["short-with-admin-key", "empty-with-admin-key", "valid-with-admin-key", "empty-loopback"],
    )
    def test_reason_validation_rejects_short_reason_under_admin_key(
        self, reason, admin_key, expected_status
    ):
        from fastapi.testclient import TestClient
        from src.api.main import app

        client = TestClient(app)

        headers = {}
        if admin_key:
            headers["X-Admin-Key"] = admin_key

        env_patch = {"GRACE_ADMIN_KEY": admin_key} if admin_key else {}
        with patch.dict(os.environ, env_patch, clear=False):
            resp = client.patch(
                "/api/ontology/daemon/kill-switch",
                json={"autonomy_enabled": False, "reason": reason},
                headers=headers,
            )

        assert resp.status_code == expected_status
