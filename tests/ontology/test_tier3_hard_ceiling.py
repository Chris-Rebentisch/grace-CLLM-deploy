"""Tier 3 hard ceiling invariant (Chunk 50, D401).

The daemon NEVER evaluates Tier 3 proposals regardless of any state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from src.ontology.agent_daemon import run_tick
from src.ontology.database import OntologyVersionRow, SchemaProposalRow, TrustScoreRow

UTC = timezone.utc


@pytest.fixture()
def db_session():
    from sqlalchemy import text
    from src.shared.database import get_session_factory
    factory = get_session_factory()
    session = factory()
    # Neutralize pre-existing pending proposals.
    session.execute(
        text("UPDATE schema_proposals SET status = 'deferred' WHERE status = 'pending'")
    )
    session.flush()
    yield session
    session.rollback()
    session.close()


def _setup_tier3_scenario(db: Session) -> SchemaProposalRow:
    """Insert trust_scores with full autonomy + a Tier 3 pending proposal."""
    for tier in (1, 2, 3):
        existing = db.query(TrustScoreRow).filter_by(tier=tier).first()
        vals = dict(
            trust_score=1.0,
            autonomy_threshold=0.5,
            autonomy_enabled=True,
            window_size=50,
            min_reviews_for_calibration=50,
            risk_tolerance=0.95,
            total_decisions=100,
            regression_detected=False,
        )
        if existing:
            for k, v in vals.items():
                setattr(existing, k, v)
        else:
            db.add(TrustScoreRow(id=uuid4(), tier=tier, **vals))

    version_id = uuid4()
    import random
    db.add(OntologyVersionRow(
        id=version_id, version_number=random.randint(100000, 999999),
        schema_json={}, schema_modules={}, hash_chain=f"test-{version_id}",
        source="test", is_active=False,
    ))
    db.flush()

    proposal = SchemaProposalRow(
        id=uuid4(),
        proposal_type="change_domain_range",
        change_tier=3,
        kgcl_command="create class 'HighRiskType'",
        proposed_diff={},
        evidence={"signal_provenance": {"signal_type": "human_initiated"}, "affected_types": []},
        raw_confidence=1.0,
        priority="high",
        status="pending",
        current_schema_version_id=version_id,
    )
    db.add(proposal)
    db.flush()
    return proposal


class TestTier3HardCeiling:

    @patch("src.ontology.agent_daemon.asyncio")
    def test_tier3_never_applied(self, mock_asyncio, db_session):
        """Tier 3 proposal with autonomy_enabled=true and trust_score=1.0
        NEVER reaches apply_proposal()."""
        proposal = _setup_tier3_scenario(db_session)

        summary = run_tick(db_session, agent_id="test-agent")

        # apply_proposal was never called for any Tier 3 proposal
        mock_asyncio.run.assert_not_called()
        db_session.refresh(proposal)
        assert proposal.status == "pending"  # Untouched

    @patch("src.ontology.agent_daemon.asyncio")
    def test_tier3_not_in_evaluated(self, mock_asyncio, db_session):
        """Tier 3 proposals are not counted in proposals_evaluated."""
        _setup_tier3_scenario(db_session)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_evaluated"] == 0

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_tier3_with_tier1_present(self, mock_asyncio, mock_event, db_session):
        """Tier 3 is skipped even when Tier 1 is processed in the same tick."""
        tier3_proposal = _setup_tier3_scenario(db_session)

        # Also add a Tier 1 proposal
        from tests.ontology.test_agent_daemon import _asyncio_run_stub, _insert_pending_proposal
        tier1_proposal = _insert_pending_proposal(db_session, tier=1)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.version_id = uuid4()
        mock_asyncio.run.side_effect = _asyncio_run_stub(result=mock_result)

        summary = run_tick(db_session, agent_id="test-agent")

        # Tier 1 was processed, Tier 3 was not
        db_session.refresh(tier3_proposal)
        assert tier3_proposal.status == "pending"
        assert summary["proposals_applied"] >= 1
