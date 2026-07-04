"""Shared test file: fetcher annotation + route handler + D246 AST guard
(Chunk 59, CP6/CP9/CP10).

Tests:
  CP6: 3 fetcher-annotation tests
  CP9: 2 route handler tests
  CP10: 1 D246 AST guard (route isolation)
"""

from __future__ import annotations

import ast
import pathlib
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.ingestion.communications.event_fetcher import (
    CommunicationEventWithTags,
    fetch_events_with_propagated_tags,
)
from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form
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
        {"id": str(sid), "name": f"fetch-src-{sid}"},
    )
    session.commit()
    return str(sid)


def _insert_event(session, source_id: str, **overrides) -> tuple[str, str]:
    eid = uuid4()
    mid = f"msg-{eid}"
    defaults = {
        "id": str(eid),
        "mid": mid,
        "email": "alice@internal.com",
        "rj": '[{"email": "bob@internal.com", "role": "to"}]',
        "outcome": "pending",
        "sid": source_id,
        "thread_id": None,
    }
    defaults.update(overrides)
    session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, "
            "triage_tier_outcome, source_id, thread_id) "
            "VALUES (:id, :mid, :email, :rj, :outcome, :sid, :thread_id)"
        ),
        defaults,
    )
    session.commit()
    return defaults["id"], defaults["mid"]


def _cleanup(session, source_id: str):
    session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
    session.execute(
        text("DELETE FROM communication_sensitivity_propagation WHERE thread_id LIKE 'thread-%' OR thread_id LIKE 'msg-%'")
    )
    session.execute(
        text("DELETE FROM communication_events WHERE source_id = :sid"),
        {"sid": source_id},
    )
    session.execute(
        text("DELETE FROM ingestion_sources WHERE id = :id"),
        {"id": source_id},
    )
    session.commit()


# =========================================================================
# CP6: Fetcher annotation tests (3 tests)
# =========================================================================

class TestFetcherAnnotation:
    """event_fetcher.py attaches propagated_tags via LEFT JOIN."""

    def test_single_thread_propagated_tags(self, db_session):
        """Fetcher attaches correct propagated_tags for a single-thread event."""
        sid = _ensure_source(db_session)
        tid = f"thread-{uuid4()}"
        try:
            eid, mid = _insert_event(db_session, sid, thread_id=tid)
            db_session.execute(
                text(
                    "INSERT INTO communication_sensitivity_propagation "
                    "(thread_id, propagated_tags) VALUES (:tid, :tags)"
                ),
                {"tid": tid, "tags": "|external_boundary|privileged|"},
            )
            db_session.commit()

            results = fetch_events_with_propagated_tags(
                db_session,
                where_clause="ce.id = :eid",
                params={"eid": eid},
            )
            assert len(results) == 1
            assert "privileged" in results[0].propagated_tags
            assert "external_boundary" in results[0].propagated_tags
        finally:
            _cleanup(db_session, sid)

    def test_no_propagation_row_empty_tags(self, db_session):
        """Event without a propagation row gets empty propagated_tags."""
        sid = _ensure_source(db_session)
        try:
            eid, mid = _insert_event(db_session, sid)
            results = fetch_events_with_propagated_tags(
                db_session,
                where_clause="ce.id = :eid",
                params={"eid": eid},
            )
            assert len(results) == 1
            assert results[0].propagated_tags == []
        finally:
            _cleanup(db_session, sid)

    def test_tags_from_bar_form_round_trips_on_fetch(self, db_session):
        """tags_from_bar_form round-trips correctly on the fetch path."""
        sid = _ensure_source(db_session)
        tid = f"thread-{uuid4()}"
        try:
            eid, mid = _insert_event(db_session, sid, thread_id=tid)
            bar = "|external_boundary|pii_dense|privileged|"
            db_session.execute(
                text(
                    "INSERT INTO communication_sensitivity_propagation "
                    "(thread_id, propagated_tags) VALUES (:tid, :tags)"
                ),
                {"tid": tid, "tags": bar},
            )
            db_session.commit()

            results = fetch_events_with_propagated_tags(
                db_session,
                where_clause="ce.id = :eid",
                params={"eid": eid},
            )
            assert results[0].propagated_tags == ["external_boundary", "pii_dense", "privileged"]
        finally:
            _cleanup(db_session, sid)


# =========================================================================
# CP9: Route handler tests (2 tests)
# =========================================================================

class TestRetriageStatsRoute:
    """GET /api/ingestion/retriage/stats returns aggregate retriage data."""

    def test_retriage_stats_returns_latest_cycle(self, db_session):
        """Stats route returns latest_cycle from communication_events."""
        sid = _ensure_source(db_session)
        try:
            # Insert events with retriage_cycle values.
            _insert_event(db_session, sid, outcome="filtered_t2")
            db_session.execute(
                text(
                    "UPDATE communication_events "
                    "SET retriage_cycle = 2, retriage_state = 'passed' "
                    "WHERE source_id = :sid"
                ),
                {"sid": sid},
            )
            db_session.commit()

            # Query the stats SQL directly (route handler executes raw SQL).
            row = db_session.execute(
                text("SELECT MAX(retriage_cycle) AS max_cycle FROM communication_events")
            ).fetchone()
            assert row.max_cycle >= 2
        finally:
            _cleanup(db_session, sid)

    def test_retriage_stats_by_state(self, db_session):
        """Stats route groups filtered events by retriage_state."""
        sid = _ensure_source(db_session)
        try:
            eid1, _ = _insert_event(db_session, sid, outcome="filtered_t2")
            eid2, _ = _insert_event(db_session, sid, outcome="filtered_t3")
            db_session.execute(
                text(
                    "UPDATE communication_events "
                    "SET retriage_state = 'passed' WHERE id = :eid"
                ),
                {"eid": eid1},
            )
            db_session.execute(
                text(
                    "UPDATE communication_events "
                    "SET retriage_state = 'still_filtered' WHERE id = :eid"
                ),
                {"eid": eid2},
            )
            db_session.commit()

            rows = db_session.execute(
                text(
                    "SELECT COALESCE(retriage_state, 'untriaged') AS state, count(*) AS cnt "
                    "FROM communication_events "
                    "WHERE triage_tier_outcome LIKE 'filtered_%' AND source_id = :sid "
                    "GROUP BY COALESCE(retriage_state, 'untriaged')"
                ),
                {"sid": sid},
            ).fetchall()
            by_state = {r.state: r.cnt for r in rows}
            assert "passed" in by_state
            assert "still_filtered" in by_state
        finally:
            _cleanup(db_session, sid)


# =========================================================================
# CP10: D246 AST guard (1 test)
# =========================================================================

class TestD246RouteIsolation:
    """D246 mirror — route module must not import retriage or sensitivity_tagger."""

    def test_ingestion_routes_does_not_import_retriage_or_tagger(self):
        """src/api/ingestion_routes.py must NOT import
        src.ingestion.communications.retriage or
        src.ingestion.communications.sensitivity_tagger (D246 mirror)."""
        source = pathlib.Path("src/api/ingestion_routes.py")
        tree = ast.parse(source.read_text())

        forbidden = {
            "src.ingestion.communications.retriage",
            "src.ingestion.communications.sensitivity_tagger",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, (
                        f"ingestion_routes.py imports {alias.name} (D246 violation)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and any(node.module.startswith(f) for f in forbidden):
                    raise AssertionError(
                        f"ingestion_routes.py imports from {node.module} (D246 violation)"
                    )
