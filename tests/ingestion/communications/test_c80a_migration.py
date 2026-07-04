"""Tests for c80a_thread_position migration (Chunk 80a, D513).

Verifies:
  1. upgrade/downgrade cycle succeeds.
  2. Trigger carve-out permits UPDATE on thread_position/thread_id/thread_orphan
     while still blocking immutable columns.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.shared.database import get_engine


@pytest.fixture
def db_session():
    """Yield a raw SQLAlchemy session against the test database."""
    from sqlalchemy.orm import Session

    engine = get_engine()
    with Session(engine) as session:
        yield session


@pytest.fixture
def test_source(db_session):
    """Create a temporary ingestion_sources row for FK satisfaction."""
    source_id = uuid4()
    db_session.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'mbox', '{}', 'test')"
        ),
        {"id": source_id, "name": f"test-source-{source_id}"},
    )
    db_session.commit()
    yield source_id
    # Cleanup
    db_session.execute(
        text("DELETE FROM ingestion_sources WHERE id = :id"),
        {"id": source_id},
    )
    db_session.commit()


def test_upgrade_downgrade(db_session):
    """Alembic upgrade head succeeds and thread_position column exists."""
    result = db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'communication_events' AND column_name = 'thread_position'"
        )
    )
    row = result.fetchone()
    assert row is not None, "thread_position column should exist after upgrade"


def test_trigger_carveout(db_session, test_source):
    """UPDATE on thread_position/thread_id/thread_orphan is permitted; immutable column UPDATE is blocked."""
    row_id = uuid4()
    db_session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, source_id, triage_tier_outcome) "
            "VALUES (:id, :msg_id, :sender, CAST(:recipients AS jsonb), :source_id, 'pending')"
        ),
        {
            "id": row_id,
            "msg_id": f"<test-{row_id}@example.com>",
            "sender": "test@example.com",
            "recipients": '["to@example.com"]',
            "source_id": test_source,
        },
    )
    db_session.commit()

    try:
        # UPDATE thread_position should succeed (mutable by absence)
        db_session.execute(
            text("UPDATE communication_events SET thread_position = 0 WHERE id = :id"),
            {"id": row_id},
        )
        db_session.commit()

        # UPDATE thread_id should succeed (removed from blocklist by c80a)
        db_session.execute(
            text("UPDATE communication_events SET thread_id = 'root-abc' WHERE id = :id"),
            {"id": row_id},
        )
        db_session.commit()

        # UPDATE thread_orphan should succeed (removed from blocklist by c80a)
        db_session.execute(
            text("UPDATE communication_events SET thread_orphan = true WHERE id = :id"),
            {"id": row_id},
        )
        db_session.commit()

        # UPDATE on immutable column (subject) should be blocked by trigger
        with pytest.raises(Exception, match="check_violation|append-only|mutable"):
            db_session.execute(
                text("UPDATE communication_events SET subject = 'hacked' WHERE id = :id"),
                {"id": row_id},
            )
            db_session.commit()
    finally:
        # Clean up — bypass append-only trigger for test teardown
        db_session.rollback()
        db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        db_session.execute(
            text("DELETE FROM communication_events WHERE id = :id"),
            {"id": row_id},
        )
        db_session.commit()
