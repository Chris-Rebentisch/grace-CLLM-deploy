"""Tests for c57a_ingest_chk_apscheduler migration (Chunk 57, D424/D425)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory


@pytest.fixture()
def db():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def test_ingestion_checkpoints_table_exists(db):
    """c57a creates ingestion_checkpoints table."""
    result = db.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'ingestion_checkpoints'")
    )
    assert result.fetchone() is not None


def test_ingestion_checkpoints_checkpoint_type_check(db):
    """c57a CHECK rejects invalid checkpoint_type values."""
    from uuid import uuid4

    source_id = uuid4()
    # First insert a source so the FK is satisfied
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(source_id), "name": f"test_chk_{source_id}"},
    )
    db.commit()

    # Valid checkpoint_type should succeed
    db.execute(
        text(
            "INSERT INTO ingestion_checkpoints (source_id, checkpoint_type, checkpoint_value) "
            "VALUES (:sid, 'uid_validity', 'test_value')"
        ),
        {"sid": str(source_id)},
    )
    db.commit()

    # Invalid checkpoint_type should fail
    source_id2 = uuid4()
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(source_id2), "name": f"test_chk_{source_id2}"},
    )
    db.commit()

    with pytest.raises(Exception, match="ck_ingestion_checkpoints_checkpoint_type"):
        db.execute(
            text(
                "INSERT INTO ingestion_checkpoints (source_id, checkpoint_type, checkpoint_value) "
                "VALUES (:sid, 'invalid_type', 'test_value')"
            ),
            {"sid": str(source_id2)},
        )
        db.commit()

    db.rollback()

    # Cleanup
    db.execute(text("DELETE FROM ingestion_checkpoints WHERE source_id = :sid"), {"sid": str(source_id)})
    db.execute(text("DELETE FROM ingestion_sources WHERE id IN (:id1, :id2)"), {"id1": str(source_id), "id2": str(source_id2)})
    db.commit()


def test_ingestion_sources_status_column_exists(db):
    """c57a adds status column to ingestion_sources."""
    result = db.execute(
        text(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name = 'ingestion_sources' AND column_name = 'status'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert "'pending'" in str(row[1])


def test_ingestion_sources_status_default(db):
    """New sources default to 'pending' status."""
    from uuid import uuid4

    source_id = uuid4()
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(source_id), "name": f"test_status_{source_id}"},
    )
    db.commit()

    result = db.execute(
        text("SELECT status FROM ingestion_sources WHERE id = :id"),
        {"id": str(source_id)},
    )
    assert result.fetchone()[0] == "pending"

    # Cleanup
    db.execute(text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": str(source_id)})
    db.commit()


def test_ingestion_checkpoints_cascade_delete(db):
    """Checkpoint rows cascade-delete when source is deleted."""
    from uuid import uuid4

    source_id = uuid4()
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(source_id), "name": f"test_cascade_{source_id}"},
    )
    db.execute(
        text(
            "INSERT INTO ingestion_checkpoints (source_id, checkpoint_type, checkpoint_value) "
            "VALUES (:sid, 'uid_validity', '12345:100')"
        ),
        {"sid": str(source_id)},
    )
    db.commit()

    # Delete source — checkpoint should cascade
    db.execute(text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": str(source_id)})
    db.commit()

    result = db.execute(
        text("SELECT 1 FROM ingestion_checkpoints WHERE source_id = :sid"),
        {"sid": str(source_id)},
    )
    assert result.fetchone() is None
