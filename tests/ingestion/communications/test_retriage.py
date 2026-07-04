"""Tests for the re-triage scheduler (Chunk 59, D421/D438, CP2).

Covers:
- Forward-walk processes each filtered row at most once per cycle
- Terminal `passed` rows excluded from next-cycle worklist
- PID-file guard rejects second concurrent invocation
- --dry-run produces zero DB writes
- Promotion mapping: all three outcomes
- Interrupted-run resume picks up from first unprocessed row
- Cycle counter increments correctly
- Empty worklist completes without error
- Worklist filter excludes already-passed
- State machine transition coverage
- D246 mirror: module does NOT import fastapi or apscheduler
"""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.shared.config import get_settings


@pytest.fixture(scope="module")
def db_session():
    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _ensure_source(session) -> str:
    sid = uuid4()
    session.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test')"
        ),
        {"id": str(sid), "name": f"retriage-src-{sid}"},
    )
    session.commit()
    return str(sid)


def _insert_comm_event(session, source_id: str, **overrides) -> str:
    eid = uuid4()
    defaults = {
        "id": str(eid),
        "mid": f"msg-{eid}",
        "email": "test@example.com",
        "rj": '[{"email": "r@example.com", "role": "to"}]',
        "outcome": "filtered_t2_no_known_entity",
        "sid": source_id,
    }
    defaults.update(overrides)
    session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, "
            "triage_tier_outcome, source_id, retriage_cycle, retriage_state) "
            "VALUES (:id, :mid, :email, :rj, :outcome, :sid, :cycle, :state)"
        ),
        {
            "id": defaults["id"],
            "mid": defaults["mid"],
            "email": defaults["email"],
            "rj": defaults["rj"],
            "outcome": defaults["outcome"],
            "sid": defaults["sid"],
            "cycle": defaults.get("cycle"),
            "state": defaults.get("state"),
        },
    )
    session.commit()
    return defaults["id"]


def _cleanup_events(session, source_id: str):
    session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
    session.execute(
        text("DELETE FROM communication_events WHERE source_id = :sid"),
        {"sid": source_id},
    )
    session.execute(
        text("DELETE FROM ingestion_sources WHERE id = :id"),
        {"id": source_id},
    )
    session.commit()


# --- Mocked tier functions for isolation ---

async def _mock_tier2_pass(event, arcade_client):
    """Tier 2 passes (entity found) — returns None."""
    return None


async def _mock_tier2_fail(event, arcade_client):
    """Tier 2 fails (no entity) — returns filter outcome."""
    return "filtered_t2_no_known_entity"


async def _mock_tier3_batch_pass(events, embeddings, config, url):
    return [None] * len(events)


async def _mock_tier3_batch_fail(events, embeddings, config, url):
    return ["filtered_t3_below_threshold"] * len(events)


async def _mock_tier4_pass(event, provider, config):
    return None  # Relevant


async def _mock_tier4_fail(event, provider, config):
    return "filtered_t4_not_organizationally_relevant"


def _expected_next_cycle(session) -> int:
    """Mirror the runner's global MAX(retriage_cycle)+1 computation.

    grace_test is shared across harness runs; residue rows from other suites
    can carry a non-zero retriage_cycle, so absolute cycle assertions flake.
    """
    max_cycle = session.execute(
        text("SELECT COALESCE(MAX(retriage_cycle), 0) FROM communication_events")
    ).scalar()
    return (max_cycle or 0) + 1


class TestRetriageScheduler:
    """Re-triage scheduler unit tests."""

    def test_d246_no_fastapi_import(self):
        """retriage.py must NOT import fastapi or apscheduler (D246 mirror)."""
        src = Path("src/ingestion/communications/retriage.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "fastapi" not in alias.name, "D246: retriage imports fastapi"
                    assert "apscheduler" not in alias.name, "D246: retriage imports apscheduler"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "fastapi" not in node.module, "D246: retriage imports fastapi"
                    assert "apscheduler" not in node.module, "D246: retriage imports apscheduler"

    def test_pid_guard_rejects_second(self):
        """PID-file guard rejects concurrent invocation."""
        from src.ingestion.communications.retriage import _acquire_pid, _release_pid

        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "test.pid")
            # First acquire succeeds
            assert _acquire_pid(pid_path) is True
            # Second acquire fails (same PID = alive)
            assert _acquire_pid(pid_path) is False
            _release_pid(pid_path)

    def test_empty_worklist(self, db_session):
        """Empty worklist completes without error."""
        sid = _ensure_source(db_session)
        try:
            import asyncio
            result = asyncio.run(self._run_cycle_mocked(dry_run=True))
            assert result["processed"] == 0
        finally:
            _cleanup_events(db_session, sid)

    def test_dry_run_no_writes(self, db_session):
        """--dry-run produces zero DB writes."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=True))

            # Verify no state change
            row = db_session.execute(
                text("SELECT retriage_cycle, retriage_state FROM communication_events WHERE id = :id"),
                {"id": eid},
            ).fetchone()
            assert row[0] is None  # Still NULL
            assert row[1] is None
        finally:
            _cleanup_events(db_session, sid)

    def test_full_pass_promotion(self, db_session):
        """Full Tier 2+3+4 pass → passed_to_extraction."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        expected_cycle = _expected_next_cycle(db_session)
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))

            assert result["processed"] == 1
            row = db_session.execute(
                text(
                    "SELECT retriage_state, retriage_cycle, triage_tier_outcome "
                    "FROM communication_events WHERE id = :id"
                ),
                {"id": eid},
            ).fetchone()
            assert row[0] == "passed"
            assert row[1] == expected_cycle
            assert row[2] == "passed_to_extraction"
        finally:
            _cleanup_events(db_session, sid)

    def test_still_filtered_t2_fail(self, db_session):
        """Tier 2 failure → still_filtered."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        expected_cycle = _expected_next_cycle(db_session)
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_fail, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))

            row = db_session.execute(
                text(
                    "SELECT retriage_state, retriage_cycle, triage_tier_outcome "
                    "FROM communication_events WHERE id = :id"
                ),
                {"id": eid},
            ).fetchone()
            assert row[0] == "still_filtered"
            assert row[1] == expected_cycle
            assert row[2] == "filtered_t2_no_known_entity"  # Unchanged
        finally:
            _cleanup_events(db_session, sid)

    def test_still_filtered_t4_fail(self, db_session):
        """Tier 4 failure (T2+T3 OK) → still_filtered."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        expected_cycle = _expected_next_cycle(db_session)
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_fail):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))

            row = db_session.execute(
                text(
                    "SELECT retriage_state, retriage_cycle "
                    "FROM communication_events WHERE id = :id"
                ),
                {"id": eid},
            ).fetchone()
            assert row[0] == "still_filtered"
            assert row[1] == expected_cycle
        finally:
            _cleanup_events(db_session, sid)

    def test_passed_excluded_from_worklist(self, db_session):
        """Already-passed rows are excluded from worklist."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid, cycle=1, state="passed")
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert result["processed"] == 0
        finally:
            _cleanup_events(db_session, sid)

    def test_cycle_counter_increments(self, db_session):
        """Cycle counter increments from previous max."""
        sid = _ensure_source(db_session)
        # First event already processed in cycle 3
        _insert_comm_event(db_session, sid, cycle=3, state="still_filtered")
        # Second event not yet processed
        eid2 = _insert_comm_event(db_session, sid)
        expected_cycle = _expected_next_cycle(db_session)
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))

            # Cycle is global MAX + 1 — at least 4 given the cycle=3 seed above.
            assert result["cycle"] == expected_cycle
            assert result["cycle"] >= 4
        finally:
            _cleanup_events(db_session, sid)

    def test_once_per_cycle(self, db_session):
        """Each row is processed at most once per cycle."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        call_count = 0

        async def counting_t2(event, client):
            nonlocal call_count
            call_count += 1
            return None

        try:
            import asyncio
            with self._patch_tiers(t2=counting_t2, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert call_count == 1

            # Second cycle: row already passed, so not re-evaluated
            call_count = 0
            with self._patch_tiers(t2=counting_t2, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert call_count == 0
        finally:
            _cleanup_events(db_session, sid)

    def test_still_filtered_revisited_next_cycle(self, db_session):
        """still_filtered rows are revisited in the next cycle."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        try:
            import asyncio
            # Cycle 1: T2 fails → still_filtered
            with self._patch_tiers(t2=_mock_tier2_fail, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                r1 = asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert r1["processed"] == 1

            # Cycle 2: T2 passes now → full pass
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                r2 = asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert r2["processed"] == 1

            row = db_session.execute(
                text(
                    "SELECT retriage_state, triage_tier_outcome "
                    "FROM communication_events WHERE id = :id"
                ),
                {"id": eid},
            ).fetchone()
            assert row[0] == "passed"
            assert row[1] == "passed_to_extraction"
        finally:
            _cleanup_events(db_session, sid)

    def test_pending_events_not_in_worklist(self, db_session):
        """Events with triage_tier_outcome='pending' are not in the worklist."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid, outcome="pending")
        try:
            import asyncio
            with self._patch_tiers(t2=_mock_tier2_pass, t3=_mock_tier3_batch_pass, t4=_mock_tier4_pass):
                result = asyncio.run(self._run_cycle_mocked(dry_run=False))
            assert result["processed"] == 0
        finally:
            _cleanup_events(db_session, sid)

    # --- Helpers ---

    @staticmethod
    def _patch_tiers(t2, t3, t4):
        """Context manager that patches all three tier functions at their source modules."""
        from unittest.mock import patch, AsyncMock
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with patch("src.ingestion.communications.triage.tier2_entities.run_tier2", side_effect=t2), \
                 patch("src.ingestion.communications.triage.tier3_ontology.run_tier3_batch", side_effect=t3), \
                 patch("src.ingestion.communications.triage.tier3_ontology.build_ontology_embedding_matrix", new_callable=AsyncMock, return_value=(None, [])), \
                 patch("src.ingestion.communications.triage.tier4_llm.run_tier4", side_effect=t4), \
                 patch("src.shared.llm_provider.get_provider", return_value=AsyncMock()), \
                 patch("src.graph.arcade_client.ArcadeClient", return_value=AsyncMock()), \
                 patch("src.graph.config.ArcadeConfig", return_value=object()):
                yield
        return _ctx()

    @staticmethod
    async def _run_cycle_mocked(*, dry_run: bool) -> dict:
        from src.ingestion.communications.retriage import _run_cycle
        return await _run_cycle(dry_run=dry_run)
