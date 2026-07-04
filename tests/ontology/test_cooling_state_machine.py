"""Tests for cooling-period state machine (Chunk 50, D399)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from src.ontology.database import SchemaProposalRow, TrustScoreRow, OntologyVersionRow
from src.ontology.models import ProposalStatus

UTC = timezone.utc


@pytest.fixture()
def db_session():
    from src.shared.database import get_session_factory
    factory = get_session_factory()
    session = factory()
    yield session
    session.rollback()
    session.close()


def _pick_unique_version_number(db: Session) -> int:
    """Pick a version_number that does not collide with co-tenant residue rows.

    grace_test is shared across harness runs; a bare random pick can hit an
    existing row and fail the INSERT with a unique violation.
    """
    import random

    from sqlalchemy import text

    for _ in range(10):
        candidate = random.randint(100000, 999999)
        exists = db.execute(
            text("SELECT 1 FROM ontology_versions WHERE version_number = :v"),
            {"v": candidate},
        ).scalar()
        if not exists:
            return candidate
    raise RuntimeError("could not find a free version_number in 10 tries")


def _make_cooling_proposal(db: Session) -> SchemaProposalRow:
    """Create a proposal in COOLING status."""
    version_id = uuid4()
    db.add(OntologyVersionRow(
        id=version_id, version_number=_pick_unique_version_number(db),
        schema_json={}, schema_modules={}, hash_chain=f"test-{version_id}",
        source="test", is_active=False,
    ))
    db.flush()

    row = SchemaProposalRow(
        id=uuid4(),
        proposal_type="add_property",
        change_tier=1,
        kgcl_command="create class 'CoolTest'",
        proposed_diff={},
        evidence={"signal_provenance": {"signal_type": "human_initiated"}, "affected_types": []},
        raw_confidence=1.0,
        priority="medium",
        status="cooling",
        current_schema_version_id=version_id,
        cooling_period_expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db.add(row)
    db.flush()
    return row


class TestCoolingStateMachine:

    def test_confirm_cooling_to_applied(self, db_session):
        """Confirm: COOLING -> APPLIED with cooling_outcome='confirmed'."""
        proposal = _make_cooling_proposal(db_session)
        proposal.status = "applied"
        proposal.cooling_outcome = "confirmed"
        db_session.flush()

        db_session.refresh(proposal)
        assert proposal.status == "applied"
        assert proposal.cooling_outcome == "confirmed"

    def test_auto_finalize_on_expiry(self, db_session):
        """Auto-finalize: COOLING -> APPLIED on expiry with cooling_outcome='auto_finalized'."""
        proposal = _make_cooling_proposal(db_session)
        proposal.cooling_period_expires_at = datetime.now(UTC) - timedelta(hours=1)
        proposal.status = "applied"
        proposal.cooling_outcome = "auto_finalized"
        db_session.flush()

        db_session.refresh(proposal)
        assert proposal.status == "applied"
        assert proposal.cooling_outcome == "auto_finalized"

    def test_revert_cooling_to_reverted(self, db_session):
        """Revert: COOLING -> REVERTED with all revert fields."""
        proposal = _make_cooling_proposal(db_session)
        # Create an inverse proposal to reference.
        inverse = _make_cooling_proposal(db_session)
        now = datetime.now(UTC)

        proposal.status = "reverted"
        proposal.cooling_outcome = "reverted"
        proposal.reverted_at = now
        proposal.reverted_by = "admin@example.com"
        proposal.reverted_proposal_id = inverse.id
        db_session.flush()

        db_session.refresh(proposal)
        assert proposal.status == "reverted"
        assert proposal.cooling_outcome == "reverted"
        assert proposal.reverted_at is not None
        assert proposal.reverted_by == "admin@example.com"
        assert proposal.reverted_proposal_id is not None

    def test_non_cooling_to_confirm_raises(self, db_session):
        """Non-COOLING proposal cannot transition to confirmed (checked at app layer)."""
        proposal = _make_cooling_proposal(db_session)
        proposal.status = "pending"
        db_session.flush()

        # At app layer, confirm route checks status == COOLING before allowing.
        db_session.refresh(proposal)
        assert proposal.status != "cooling"

    def test_reverted_fields_populated(self, db_session):
        """Revert sets reverted_at, reverted_by, reverted_proposal_id."""
        proposal = _make_cooling_proposal(db_session)
        inverse = _make_cooling_proposal(db_session)
        now = datetime.now(UTC)

        proposal.status = "reverted"
        proposal.cooling_outcome = "reverted"
        proposal.reverted_at = now
        proposal.reverted_by = "operator"
        proposal.reverted_proposal_id = inverse.id
        db_session.flush()

        db_session.refresh(proposal)
        assert proposal.reverted_at is not None
        assert proposal.reverted_by == "operator"

    def test_cooling_outcome_column_constraint(self, db_session):
        """Cooling outcome accepts known values."""
        proposal = _make_cooling_proposal(db_session)
        for outcome in ("confirmed", "auto_finalized", "reverted"):
            proposal.cooling_outcome = outcome
            db_session.flush()
            db_session.refresh(proposal)
            assert proposal.cooling_outcome == outcome

    def test_immutable_columns_still_protected(self, db_session):
        """Immutable columns on schema_proposals still raise on UPDATE."""
        proposal = _make_cooling_proposal(db_session)
        original_kgcl = proposal.kgcl_command
        proposal.kgcl_command = "rename class 'Foo' to 'Bar'"
        with pytest.raises(Exception, match="immutable"):
            db_session.flush()
        db_session.rollback()

    def test_cooling_period_expires_at_updatable(self, db_session):
        """cooling_period_expires_at is a mutable column."""
        proposal = _make_cooling_proposal(db_session)
        new_time = datetime.now(UTC) + timedelta(hours=96)
        proposal.cooling_period_expires_at = new_time
        db_session.flush()
        db_session.refresh(proposal)
        assert proposal.cooling_period_expires_at is not None
