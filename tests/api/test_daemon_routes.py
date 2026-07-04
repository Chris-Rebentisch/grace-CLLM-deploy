"""Tests for agent daemon API routes (Chunk 50, D398/D399/D400)."""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.ontology.database import (
    OntologyVersionRow,
    SchemaProposalRow,
    TrustScoreRow,
)
from src.shared.database import get_session_factory

UTC = timezone.utc
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


@pytest.fixture(autouse=True)
def _clean_kill_switch_history():
    """F-027 / ISS-0010: disengage without an open engage row now 409s, so
    kill-switch tests must start from a deterministic no-open-row state."""
    _cleanup_history()
    yield
    _cleanup_history()


@pytest.fixture()
def db_session():
    factory = get_session_factory()
    session = factory()
    yield session
    session.rollback()
    session.close()


def _ensure_trust_scores(db, **overrides):
    """Ensure trust_score rows exist for tiers 1, 2, 3."""
    defaults = {
        "trust_score": 0.98,
        "autonomy_threshold": 0.95,
        "autonomy_enabled": True,
        "window_size": 50,
        "min_reviews_for_calibration": 50,
        "risk_tolerance": 0.95,
        "total_decisions": 100,
        "regression_detected": False,
    }
    for tier in (1, 2, 3):
        existing = db.query(TrustScoreRow).filter_by(tier=tier).first()
        if existing:
            for k, v in {**defaults, **overrides}.items():
                setattr(existing, k, v)
        else:
            db.add(TrustScoreRow(id=uuid4(), tier=tier, **{**defaults, **overrides}))
    db.commit()


def _make_cooling_proposal(db) -> SchemaProposalRow:
    version_id = uuid4()
    db.add(OntologyVersionRow(
        id=version_id, version_number=random.randint(100000, 999999),
        schema_json={}, schema_modules={}, hash_chain=f"test-{version_id}",
        source="test", is_active=False,
    ))
    db.flush()
    row = SchemaProposalRow(
        id=uuid4(),
        proposal_type="add_property",
        change_tier=1,
        kgcl_command="create class 'TestRevert'",
        proposed_diff={},
        evidence={"signal_provenance": {"signal_type": "human_initiated"}, "affected_types": []},
        raw_confidence=1.0,
        priority="medium",
        status="cooling",
        current_schema_version_id=version_id,
        cooling_period_expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db.add(row)
    db.commit()
    return row


class TestKillSwitch:

    def test_patch_writes_all_tiers(self, db_session):
        """Kill switch writes all three tiers."""
        _ensure_trust_scores(db_session, autonomy_enabled=True)
        resp = client.patch(
            "/api/ontology/daemon/kill-switch",
            json={"autonomy_enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["autonomy_enabled"] is False
        assert data["tiers_updated"] == 3

    def test_patch_auth_enforcement(self):
        """Kill switch requires admin key when set."""
        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "testkey123"}):
            resp = client.patch(
                "/api/ontology/daemon/kill-switch",
                json={"autonomy_enabled": False},
            )
            assert resp.status_code == 401

    def test_patch_with_valid_key(self, db_session):
        """Kill switch succeeds with valid admin key.

        F-027 / ISS-0010: disengage now requires an open engage row (no more
        legacy blanket-enable), so engage first, then disengage with the key.
        """
        _ensure_trust_scores(db_session)
        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "testkey123"}):
            resp_engage = client.patch(
                "/api/ontology/daemon/kill-switch",
                json={"autonomy_enabled": False, "reason": "test admin-key engage reason"},
                headers={"X-Admin-Key": "testkey123"},
            )
            assert resp_engage.status_code == 200
            resp = client.patch(
                "/api/ontology/daemon/kill-switch",
                json={"autonomy_enabled": True, "reason": "test admin-key disengage now"},
                headers={"X-Admin-Key": "testkey123"},
            )
            assert resp.status_code == 200


class TestDaemonStatus:

    def test_status_get(self, db_session):
        """Daemon status GET returns payload."""
        _ensure_trust_scores(db_session)
        resp = client.get("/api/ontology/daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "proposals_in_cooling" in data
        assert "kill_switch_engaged" in data
        assert "tiers" in data


class TestConfirm:

    def test_confirm_cooling_to_applied(self, db_session):
        """Confirm POST COOLING -> APPLIED."""
        proposal = _make_cooling_proposal(db_session)
        resp = client.post(f"/api/ontology/daemon/{proposal.id}/confirm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert data["cooling_outcome"] == "confirmed"

    def test_confirm_non_cooling_409(self, db_session):
        """Confirm POST non-COOLING -> 409."""
        proposal = _make_cooling_proposal(db_session)
        proposal.status = "applied"
        db_session.commit()

        resp = client.post(f"/api/ontology/daemon/{proposal.id}/confirm")
        assert resp.status_code == 409


class TestRevert:

    def test_revert_non_cooling_409(self, db_session):
        """Revert POST non-COOLING -> 409."""
        proposal = _make_cooling_proposal(db_session)
        proposal.status = "applied"
        db_session.commit()

        resp = client.post(
            f"/api/ontology/daemon/{proposal.id}/revert",
            json={"reverted_by": "admin"},
        )
        assert resp.status_code == 409

    def test_revert_non_revertible_422(self, db_session):
        """Revert POST non-revertible command -> 422."""
        # Create a proposal directly with a non-revertible command.
        version_id = uuid4()
        db_session.add(OntologyVersionRow(
            id=version_id, version_number=random.randint(100000, 999999),
            schema_json={}, schema_modules={}, hash_chain=f"test-{version_id}",
            source="test", is_active=False,
        ))
        db_session.flush()
        row = SchemaProposalRow(
            id=uuid4(),
            proposal_type="add_synonym",
            change_tier=1,
            kgcl_command="add synonym 'alias' for class 'Foo'",
            proposed_diff={},
            evidence={"signal_provenance": {"signal_type": "human_initiated"}, "affected_types": []},
            raw_confidence=1.0,
            priority="medium",
            status="cooling",
            current_schema_version_id=version_id,
            cooling_period_expires_at=datetime.now(UTC) + timedelta(hours=48),
        )
        db_session.add(row)
        db_session.commit()

        resp = client.post(
            f"/api/ontology/daemon/{row.id}/revert",
            json={"reverted_by": "admin"},
        )
        assert resp.status_code == 422

    def test_admin_key_enforcement(self):
        """Mutating routes require admin key when set."""
        fake_id = uuid4()
        with patch.dict(os.environ, {"GRACE_ADMIN_KEY": "testkey123"}):
            resp = client.post(
                f"/api/ontology/daemon/{fake_id}/confirm",
            )
            assert resp.status_code == 401
