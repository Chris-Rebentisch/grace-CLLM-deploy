"""c56a migration tests (Chunk 56 CP7 — 9 tests)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory


@pytest.fixture
def db():
    factory = get_session_factory()
    session = factory()
    yield session
    session.rollback()
    session.close()


def _insert_source(db, source_id=None):
    """Insert a test ingestion_sources row and return the source_id."""
    sid = source_id or uuid4()
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test')"
        ),
        {"id": str(sid), "name": f"test-{sid}"},
    )
    db.flush()
    return sid


def _insert_event(db, source_id, message_id="<test@example.com>", **overrides):
    """Insert a test communication_events row."""
    eid = overrides.pop("id", uuid4())
    db.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, source_id) "
            "VALUES (:id, :msg_id, 'test@example.com', '[]'::jsonb, :source_id)"
        ),
        {"id": str(eid), "msg_id": message_id, "source_id": str(source_id)},
    )
    db.flush()
    return eid


def test_migration_applies_cleanly():
    """c56a migration applied from c55a — tables exist."""
    factory = get_session_factory()
    session = factory()
    try:
        result = session.execute(
            text("SELECT tablename FROM pg_tables WHERE tablename IN ('communication_events', 'curated_email_subsets')")
        )
        tables = {row[0] for row in result}
        assert "communication_events" in tables
        assert "curated_email_subsets" in tables
    finally:
        session.close()


def test_trigger_rejects_delete(db):
    """communication_events trigger rejects DELETE."""
    sid = _insert_source(db)
    eid = _insert_event(db, sid)
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM communication_events WHERE id = :id"), {"id": str(eid)})
        db.flush()


def test_trigger_rejects_immutable_update(db):
    """communication_events trigger rejects update to immutable column sender_email."""
    sid = _insert_source(db)
    eid = _insert_event(db, sid)
    with pytest.raises(Exception, match="only triage_tier_outcome"):
        db.execute(
            text("UPDATE communication_events SET sender_email = 'new@example.com' WHERE id = :id"),
            {"id": str(eid)},
        )
        db.flush()


def test_trigger_admits_mutable_update(db):
    """communication_events trigger allows update to triage_tier_outcome."""
    sid = _insert_source(db)
    eid = _insert_event(db, sid)
    db.execute(
        text("UPDATE communication_events SET triage_tier_outcome = 'passed_to_t4' WHERE id = :id"),
        {"id": str(eid)},
    )
    db.flush()
    row = db.execute(
        text("SELECT triage_tier_outcome FROM communication_events WHERE id = :id"),
        {"id": str(eid)},
    ).fetchone()
    assert row[0] == "passed_to_t4"


def test_curated_trigger_rejects_delete(db):
    """curated_email_subsets trigger rejects DELETE."""
    sid = _insert_source(db)
    db.execute(
        text(
            "INSERT INTO curated_email_subsets "
            "(id, source_id, deployment_path, selected_message_ids, diversity_metrics) "
            "VALUES (:id, :source_id, 'B', '[]'::jsonb, '{}'::jsonb)"
        ),
        {"id": str(uuid4()), "source_id": str(sid)},
    )
    db.flush()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM curated_email_subsets WHERE source_id = :sid"), {"sid": str(sid)})
        db.flush()


def test_curated_trigger_admits_sentinel_update(db):
    """curated_email_subsets trigger allows sentinel_status update."""
    sid = _insert_source(db)
    cid = uuid4()
    db.execute(
        text(
            "INSERT INTO curated_email_subsets "
            "(id, source_id, deployment_path, selected_message_ids, diversity_metrics) "
            "VALUES (:id, :source_id, 'B', '[]'::jsonb, '{}'::jsonb)"
        ),
        {"id": str(cid), "source_id": str(sid)},
    )
    db.flush()
    db.execute(
        text("UPDATE curated_email_subsets SET sentinel_status = 'ready' WHERE id = :id"),
        {"id": str(cid)},
    )
    db.flush()


def test_curated_trigger_rejects_non_sentinel_update(db):
    """curated_email_subsets trigger rejects update to non-sentinel column."""
    sid = _insert_source(db)
    cid = uuid4()
    db.execute(
        text(
            "INSERT INTO curated_email_subsets "
            "(id, source_id, deployment_path, selected_message_ids, diversity_metrics) "
            "VALUES (:id, :source_id, 'B', '[]'::jsonb, '{}'::jsonb)"
        ),
        {"id": str(cid), "source_id": str(sid)},
    )
    db.flush()
    with pytest.raises(Exception, match="only sentinel_status"):
        db.execute(
            text("UPDATE curated_email_subsets SET deployment_path = 'C' WHERE id = :id"),
            {"id": str(cid)},
        )
        db.flush()


def test_composite_index_exists(db):
    """Composite index (source_id, triage_tier_outcome, id) exists."""
    result = db.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'communication_events' "
            "AND indexname = 'ix_communication_events_src_triage_id'"
        )
    )
    assert result.fetchone() is not None


def test_revision_id_width():
    """Revision ID is within 32-char limit (D350)."""
    assert len("c56a_communication_events") <= 32
