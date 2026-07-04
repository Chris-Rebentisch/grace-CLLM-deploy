"""Tests for c59a_retriage_sensitivity migration (Chunk 59, D441).

Covers:
- Upgrade: columns and table exist
- communication_events trigger: 3 existing + 2 new mutable columns admitted;
  DELETE blocked; immutable-column UPDATE blocked
- communication_sensitivity_propagation trigger: mutable columns admitted;
  DELETE blocked; immutable-column UPDATE blocked
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.shared.config import get_settings


@pytest.fixture(scope="module")
def db_session():
    """Create a test DB session scoped to the module."""
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
        {"id": str(sid), "name": f"test-src-{sid}"},
    )
    return str(sid)


def _insert_comm_event(session, source_id: str) -> str:
    eid = uuid4()
    session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, triage_tier_outcome, source_id) "
            "VALUES (:id, :mid, 'test@example.com', :rj, 'pending', :sid)"
        ),
        {
            "id": str(eid),
            "mid": f"msg-{eid}",
            "rj": '["r@example.com"]',
            "sid": source_id,
        },
    )
    return str(eid)


class TestC59aMigration:
    """c59a_retriage_sensitivity migration tests."""

    def test_columns_and_table_exist(self, db_session):
        """New columns on communication_events + gap_reports; new table exists."""
        rows = db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'communication_events' "
                "AND column_name IN ('retriage_cycle', 'retriage_state') "
                "ORDER BY column_name"
            )
        ).fetchall()
        assert {r[0] for r in rows} == {"retriage_cycle", "retriage_state"}

        row = db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'gap_reports' AND column_name = 'mixed_source_coverage'"
            )
        ).fetchone()
        assert row is not None

        row = db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'communication_sensitivity_propagation'"
            )
        ).fetchone()
        assert row is not None

        # Index existence
        idx_names = {
            r[0]
            for r in db_session.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename IN "
                    "('communication_events', 'gap_reports', 'communication_sensitivity_propagation')"
                )
            ).fetchall()
        }
        assert "ix_communication_events_retriage_cycle_state" in idx_names
        assert "ix_gap_reports_mixed_source_coverage" in idx_names

    def test_ce_trigger_admits_all_mutable_columns(self, db_session):
        """All 5 mutable columns admit UPDATE."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        db_session.commit()
        try:
            db_session.execute(
                text(
                    "UPDATE communication_events SET "
                    "triage_tier_outcome = 'passed_to_extraction', "
                    "sensitivity_tags = '|privileged|', "
                    "observed_in_sources_json = '{\"a\": 1}', "
                    "retriage_cycle = 1, retriage_state = 'passed' "
                    "WHERE id = :id"
                ),
                {"id": eid},
            )
            db_session.commit()

            row = db_session.execute(
                text(
                    "SELECT retriage_cycle, retriage_state "
                    "FROM communication_events WHERE id = :id"
                ),
                {"id": eid},
            ).fetchone()
            assert row[0] == 1
            assert row[1] == "passed"
        finally:
            db_session.rollback()
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            db_session.execute(
                text("DELETE FROM communication_events WHERE id = :id"), {"id": eid}
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": sid}
            )
            db_session.commit()

    def test_ce_trigger_rejects_delete(self, db_session):
        """DELETE on communication_events is blocked."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        db_session.commit()
        try:
            with pytest.raises(Exception, match="append-only"):
                db_session.execute(
                    text("DELETE FROM communication_events WHERE id = :id"),
                    {"id": eid},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            db_session.execute(
                text("DELETE FROM communication_events WHERE id = :id"), {"id": eid}
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": sid}
            )
            db_session.commit()

    def test_ce_trigger_rejects_immutable_column_update(self, db_session):
        """UPDATE on an immutable column is blocked."""
        sid = _ensure_source(db_session)
        eid = _insert_comm_event(db_session, sid)
        db_session.commit()
        try:
            with pytest.raises(Exception, match="only triage_tier_outcome"):
                db_session.execute(
                    text(
                        "UPDATE communication_events SET subject = 'hacked' WHERE id = :id"
                    ),
                    {"id": eid},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            db_session.execute(
                text("DELETE FROM communication_events WHERE id = :id"), {"id": eid}
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": sid}
            )
            db_session.commit()

    def test_csp_trigger_behavior(self, db_session):
        """communication_sensitivity_propagation trigger behavior:
        - UPDATE on propagated_tags + last_recomputed_at admitted
        - DELETE blocked
        - UPDATE on immutable column (propagated_at) blocked
        """
        tid1 = f"thread-{uuid4()}"
        tid2 = f"thread-{uuid4()}"
        tid3 = f"thread-{uuid4()}"

        # --- Mutable update admitted ---
        db_session.execute(
            text(
                "INSERT INTO communication_sensitivity_propagation "
                "(thread_id, propagated_tags) VALUES (:tid, :tags)"
            ),
            {"tid": tid1, "tags": "|privileged|"},
        )
        db_session.commit()

        db_session.execute(
            text(
                "UPDATE communication_sensitivity_propagation SET "
                "propagated_tags = '|pii_dense|privileged|', "
                "last_recomputed_at = NOW() "
                "WHERE thread_id = :tid"
            ),
            {"tid": tid1},
        )
        db_session.commit()

        row = db_session.execute(
            text(
                "SELECT propagated_tags FROM communication_sensitivity_propagation "
                "WHERE thread_id = :tid"
            ),
            {"tid": tid1},
        ).fetchone()
        assert row[0] == "|pii_dense|privileged|"

        # --- DELETE blocked ---
        db_session.execute(
            text(
                "INSERT INTO communication_sensitivity_propagation "
                "(thread_id, propagated_tags) VALUES (:tid, '')"
            ),
            {"tid": tid2},
        )
        db_session.commit()
        try:
            with pytest.raises(Exception, match="append-only"):
                db_session.execute(
                    text(
                        "DELETE FROM communication_sensitivity_propagation "
                        "WHERE thread_id = :tid"
                    ),
                    {"tid": tid2},
                )
                db_session.commit()
        finally:
            db_session.rollback()

        # --- Immutable update blocked ---
        db_session.execute(
            text(
                "INSERT INTO communication_sensitivity_propagation "
                "(thread_id, propagated_tags) VALUES (:tid, '')"
            ),
            {"tid": tid3},
        )
        db_session.commit()
        try:
            with pytest.raises(Exception, match="only propagated_tags"):
                db_session.execute(
                    text(
                        "UPDATE communication_sensitivity_propagation SET "
                        "propagated_at = '2020-01-01'::timestamptz "
                        "WHERE thread_id = :tid"
                    ),
                    {"tid": tid3},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            # Cleanup
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            for tid in (tid1, tid2, tid3):
                db_session.execute(
                    text(
                        "DELETE FROM communication_sensitivity_propagation "
                        "WHERE thread_id = :tid"
                    ),
                    {"tid": tid},
                )
            db_session.commit()
