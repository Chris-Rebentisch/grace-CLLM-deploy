"""Tests for agent daemon tick logic (Chunk 50, D398)."""

from __future__ import annotations

import os
import signal
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.ontology.agent_daemon import (
    _acquire_pid,
    _load_config,
    _make_agent_id,
    _release_pid,
    run_tick,
)
from src.ontology.database import (
    GovernanceDecisionEventRow,
    SchemaProposalRow,
    TrustScoreRow,
)
from src.ontology.models import ProposalStatus

UTC = timezone.utc


def _asyncio_run_stub(*, result=None, exc: BaseException | None = None):
    """Stand in for ``asyncio.run`` in daemon tests without leaking coroutines.

    ``run_tick`` evaluates ``apply_proposal(...)`` before calling ``run``, which
    builds a coroutine object. A plain ``MagicMock`` return_value leaves that
    coroutine un-awaited and pytest warns during teardown.
    """
    if (result is not None) == (exc is not None):
        raise ValueError("specify exactly one of result= or exc=")

    def _runner(coro):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        if exc is not None:
            raise exc
        return result

    return _runner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """Real Postgres session against the grace database.

    Neutralizes pre-existing pending proposals so daemon tests
    only see rows inserted by the test itself.
    """
    from sqlalchemy import text
    from src.shared.database import get_session_factory
    factory = get_session_factory()
    session = factory()
    # Move any pre-existing pending proposals to 'deferred' so they
    # don't interfere with daemon tick tests.
    session.execute(
        text("UPDATE schema_proposals SET status = 'deferred' WHERE status = 'pending'")
    )
    session.flush()
    yield session
    session.rollback()
    session.close()


def _insert_trust_scores(db: Session, overrides: dict | None = None) -> None:
    """Upsert trust_score rows for tiers 1, 2, 3 with sensible defaults."""
    from sqlalchemy import update as sa_update
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
    overrides = overrides or {}
    for tier in (1, 2, 3):
        tier_overrides = overrides.get(tier, {})
        merged = {**defaults, **tier_overrides}
        existing = db.query(TrustScoreRow).filter_by(tier=tier).first()
        if existing:
            for k, v in merged.items():
                setattr(existing, k, v)
        else:
            row = TrustScoreRow(id=uuid4(), tier=tier, **merged)
            db.add(row)
    db.flush()


def _insert_pending_proposal(
    db: Session, *, tier: int = 1, kgcl: str = "create class 'TestType'"
) -> SchemaProposalRow:
    """Insert a pending proposal for testing."""
    # Need a valid ontology_versions FK — use existing or create stub.
    import random
    from src.ontology.database import OntologyVersionRow
    version_id = uuid4()
    db.add(OntologyVersionRow(
        id=version_id,
        version_number=random.randint(100000, 999999),
        schema_json={},
        schema_modules={},
        hash_chain=f"test-{version_id}",
        source="test",
        is_active=False,
    ))
    db.flush()

    row = SchemaProposalRow(
        id=uuid4(),
        proposal_type="add_entity_type" if tier == 2 else "add_property",
        change_tier=tier,
        kgcl_command=kgcl,
        proposed_diff={},
        evidence={"signal_provenance": {"signal_type": "human_initiated"}, "affected_types": []},
        raw_confidence=1.0,
        priority="medium",
        status="pending",
        current_schema_version_id=version_id,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDaemonTick:
    """Tests for run_tick() logic."""

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_happy_path_tier1(self, mock_asyncio, mock_event, db_session):
        """Eligible Tier 1 proposal -> approved -> APPLIED -> COOLING."""
        _insert_trust_scores(db_session)
        proposal = _insert_pending_proposal(db_session, tier=1)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.version_id = uuid4()
        mock_asyncio.run.side_effect = _asyncio_run_stub(result=mock_result)

        now = datetime.now(UTC)
        summary = run_tick(
            db_session, agent_id="test-agent", cooling_period_hours=48,
            observation_time=now,
        )

        assert summary["proposals_applied"] >= 1
        db_session.refresh(proposal)
        assert proposal.status == "cooling"
        assert proposal.cooling_period_expires_at is not None
        assert proposal.applied_autonomously is True

    def test_kill_switch_gate(self, db_session):
        """All autonomy_enabled=false -> tick exits without evaluating."""
        _insert_trust_scores(db_session, overrides={
            1: {"autonomy_enabled": False},
            2: {"autonomy_enabled": False},
            3: {"autonomy_enabled": False},
        })
        _insert_pending_proposal(db_session, tier=1)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_evaluated"] == 0
        assert summary["proposals_applied"] == 0

    def test_regression_gate(self, db_session):
        """regression_detected=true -> daemon skips that tier."""
        _insert_trust_scores(db_session, overrides={
            1: {"regression_detected": True},
        })
        _insert_pending_proposal(db_session, tier=1)

        summary = run_tick(db_session, agent_id="test-agent")
        assert 1 in summary["suspended_tiers"]
        assert summary["proposals_applied"] == 0

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_revertible_filter(self, mock_asyncio, mock_event, db_session):
        """Non-revertible proposal excluded (add synonym)."""
        _insert_trust_scores(db_session)
        # Add synonym is non-revertible
        _insert_pending_proposal(
            db_session, tier=1, kgcl="add synonym 'alias' for class 'Foo'"
        )

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] == 0

    def test_dry_run(self, db_session):
        """Dry-run does not write to DB."""
        _insert_trust_scores(db_session)
        proposal = _insert_pending_proposal(db_session, tier=1)

        summary = run_tick(db_session, agent_id="test-agent", dry_run=True)
        db_session.refresh(proposal)
        assert proposal.status == "pending"

    def test_agent_id_format(self):
        """Agent ID has expected format."""
        aid = _make_agent_id()
        assert aid.startswith("agent-daemon-")
        assert str(os.getpid()) in aid

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_multiple_proposals_per_tick(self, mock_asyncio, mock_event, db_session):
        """Multiple eligible proposals are processed in one tick."""
        _insert_trust_scores(db_session)
        p1 = _insert_pending_proposal(db_session, tier=1, kgcl="create class 'A'")
        p2 = _insert_pending_proposal(db_session, tier=1, kgcl="create class 'B'")

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.version_id = uuid4()
        mock_asyncio.run.side_effect = _asyncio_run_stub(result=mock_result)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] >= 2

    @patch("src.ontology.agent_daemon._record_governance_event")
    def test_cooling_expiry_auto_finalize(self, mock_event, db_session):
        """Expired cooling -> auto-finalize to APPLIED."""
        _insert_trust_scores(db_session)
        proposal = _insert_pending_proposal(db_session, tier=1)
        # Manually set to cooling with expired timestamp.
        proposal.status = "cooling"
        proposal.cooling_period_expires_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.flush()

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["cooling_finalized"] >= 1
        db_session.refresh(proposal)
        assert proposal.status == "applied"
        assert proposal.cooling_outcome == "auto_finalized"

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_concurrent_api_skip(self, mock_asyncio, mock_event, db_session):
        """Status changed between evaluation -> skip gracefully (no crash)."""
        _insert_trust_scores(db_session)
        proposal = _insert_pending_proposal(db_session, tier=1)

        # Simulate apply raising an exception (as if status was changed concurrently)
        mock_asyncio.run.side_effect = _asyncio_run_stub(
            exc=Exception("concurrent status change"),
        )

        # Should not crash
        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] == 0

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_tier2_proposal(self, mock_asyncio, mock_event, db_session):
        """Tier 2 proposals are also evaluated."""
        _insert_trust_scores(db_session)
        proposal = _insert_pending_proposal(
            db_session, tier=2, kgcl="create relationship 'employs'"
        )

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.version_id = uuid4()
        mock_asyncio.run.side_effect = _asyncio_run_stub(result=mock_result)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] >= 1


class TestPidFile:
    """Tests for PID file guard."""

    def test_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "test.pid")
            assert _acquire_pid(pid_path) is True
            _release_pid(pid_path)
            assert not os.path.exists(pid_path)

    def test_running_pid_blocks(self):
        """Existing running PID -> returns False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "test.pid")
            # Write our own PID (guaranteed alive).
            with open(pid_path, "w") as f:
                f.write(str(os.getpid()))
            assert _acquire_pid(pid_path) is False
            os.remove(pid_path)

    def test_stale_pid_overwritten(self):
        """Stale PID (dead process) -> overwritten."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "test.pid")
            # Write a PID that doesn't exist.
            with open(pid_path, "w") as f:
                f.write("999999")
            assert _acquire_pid(pid_path) is True
            _release_pid(pid_path)


class TestDaemonConfig:
    """Tests for configuration loading."""

    def test_load_config_returns_dict(self):
        config = _load_config()
        assert isinstance(config, dict)
        assert "tick_interval_seconds" in config

    def test_load_config_defaults(self):
        config = _load_config()
        assert config.get("cooling_period_hours") == 48

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_trust_below_threshold(self, mock_asyncio, mock_event, db_session):
        """Trust score below threshold -> proposals not applied."""
        _insert_trust_scores(db_session, overrides={
            1: {"trust_score": 0.5, "autonomy_threshold": 0.95},
        })
        _insert_pending_proposal(db_session, tier=1)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] == 0

    @patch("src.ontology.agent_daemon._record_governance_event")
    @patch("src.ontology.agent_daemon.asyncio")
    def test_apply_failure_continues(self, mock_asyncio, mock_event, db_session):
        """apply_proposal returning success=False -> skip, don't crash."""
        _insert_trust_scores(db_session)
        _insert_pending_proposal(db_session, tier=1)

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "test error"
        mock_asyncio.run.side_effect = _asyncio_run_stub(result=mock_result)

        summary = run_tick(db_session, agent_id="test-agent")
        assert summary["proposals_applied"] == 0
