"""Tests for checkpoint_manager load/flush UPSERT (Chunk 57, CP2)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from uuid import uuid4

from src.ingestion.communications.checkpoint_manager import flush_checkpoint, load_checkpoint
from src.shared.database import get_session_factory


@pytest.fixture()
def db():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def source_id(db):
    """Create a temporary ingestion source and return its ID."""
    sid = uuid4()
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(sid), "name": f"chk_test_{sid}"},
    )
    db.commit()
    yield sid
    # Cleanup — cascades to ingestion_checkpoints
    db.execute(text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": str(sid)})
    db.commit()


def test_load_returns_none_on_fresh_source(db, source_id):
    """load_checkpoint returns None when no checkpoint exists."""
    result = load_checkpoint(db, source_id)
    assert result is None


def test_flush_and_load_round_trip(db, source_id):
    """flush + load returns the stored checkpoint."""
    flush_checkpoint(db, source_id, "uid_validity", "12345:100")
    result = load_checkpoint(db, source_id)
    assert result is not None
    assert result.checkpoint_type == "uid_validity"
    assert result.value == "12345:100"


def test_upsert_overwrites_on_second_flush(db, source_id):
    """Second flush overwrites the first (UPSERT)."""
    flush_checkpoint(db, source_id, "uid_validity", "12345:100")
    flush_checkpoint(db, source_id, "uid_validity", "12345:200")
    result = load_checkpoint(db, source_id)
    assert result is not None
    assert result.value == "12345:200"


def test_last_synced_at_updates(db, source_id):
    """last_synced_at advances on each flush."""
    flush_checkpoint(db, source_id, "delta_link", "https://example.com/delta1")
    row1 = db.execute(
        text("SELECT last_synced_at FROM ingestion_checkpoints WHERE source_id = :sid"),
        {"sid": str(source_id)},
    ).fetchone()

    flush_checkpoint(db, source_id, "delta_link", "https://example.com/delta2")
    row2 = db.execute(
        text("SELECT last_synced_at FROM ingestion_checkpoints WHERE source_id = :sid"),
        {"sid": str(source_id)},
    ).fetchone()

    assert row2[0] >= row1[0]
