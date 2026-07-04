"""Tests for c79a_comm_event_extract migration (CP3, D512)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.shared.database import get_engine


@pytest.fixture()
def db_session():
    """Yield a session against the test database."""
    engine = get_engine()
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_upgrade_columns_exist(db_session: Session):
    """After upgrade, extraction_status/extraction_event_id/extracted_at columns exist."""
    result = db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'communication_events' "
            "AND column_name IN ('extraction_status', 'extraction_event_id', 'extracted_at') "
            "ORDER BY column_name"
        )
    ).fetchall()
    col_names = [r[0] for r in result]
    assert "extracted_at" in col_names
    assert "extraction_event_id" in col_names
    assert "extraction_status" in col_names


def test_trigger_carveout(db_session: Session):
    """UPDATE on extraction_status/extraction_event_id/extracted_at is allowed;
    UPDATE on immutable columns (e.g. message_id) is blocked by trigger."""
    # Insert a test ingestion_source and communication event
    msg_id = f"test-c79a-{uuid.uuid4().hex[:8]}@example.com"
    source_id = str(uuid.uuid4())
    source_name = f"test-c79a-{uuid.uuid4().hex[:8]}"
    db_session.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment, status) "
            "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test', 'pending')"
        ),
        {"id": source_id, "name": source_name},
    )
    db_session.execute(
        text(
            "INSERT INTO communication_events "
            "(message_id, sender_email, source_id, ingested_at, recipients_json) "
            "VALUES (:msg_id, 'test@example.com', :source_id, NOW(), '[]'::jsonb)"
        ),
        {"msg_id": msg_id, "source_id": source_id},
    )
    db_session.commit()

    # UPDATE on mutable extraction columns should succeed
    event_id = str(uuid.uuid4())
    db_session.execute(
        text(
            "UPDATE communication_events "
            "SET extraction_status = 'extracted', "
            "    extraction_event_id = :event_id, "
            "    extracted_at = :ts "
            "WHERE message_id = :msg_id"
        ),
        {
            "event_id": event_id,
            "msg_id": msg_id,
            "ts": datetime.now(timezone.utc),
        },
    )
    db_session.commit()

    # Verify the update stuck
    row = db_session.execute(
        text(
            "SELECT extraction_status, extraction_event_id "
            "FROM communication_events WHERE message_id = :msg_id"
        ),
        {"msg_id": msg_id},
    ).fetchone()
    assert row[0] == "extracted"
    assert str(row[1]) == event_id

    # UPDATE on immutable column should be blocked by trigger
    with pytest.raises(Exception, match="append-only|immutable|mutable"):
        db_session.execute(
            text(
                "UPDATE communication_events "
                "SET message_id = 'hacked@evil.com' "
                "WHERE message_id = :msg_id"
            ),
            {"msg_id": msg_id},
        )
        db_session.commit()

    db_session.rollback()

    # Cleanup
    db_session.execute(
        text("SET LOCAL alembic.downgrading = 'true'")
    )
    db_session.execute(
        text("DELETE FROM communication_events WHERE message_id = :msg_id"),
        {"msg_id": msg_id},
    )
    db_session.execute(
        text("DELETE FROM ingestion_sources WHERE id = :id"),
        {"id": source_id},
    )
    db_session.commit()
