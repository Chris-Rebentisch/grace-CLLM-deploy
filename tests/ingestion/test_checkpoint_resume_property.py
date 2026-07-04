"""Hypothesis property tests for checkpoint resume invariants (Chunk 57, CP2).

Four invariants:
1. Idempotency — flushing the same value twice leaves one row.
2. Monotonic cursor — checkpoint value can only advance (caller invariant).
3. Crash-resumes do not skip — loading after flush returns at-or-before the flushed value.
4. Crash-resumes do not double-count — flush is idempotent on same value.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st
from sqlalchemy import text
from uuid import uuid4

from src.ingestion.communications.checkpoint_manager import flush_checkpoint, load_checkpoint
from src.shared.database import get_session_factory


def _create_source(db, source_id):
    db.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
            "VALUES (:id, :name, 'imap', '{}'::jsonb, 'test')"
        ),
        {"id": str(source_id), "name": f"hyp_{source_id}"},
    )
    db.commit()


def _cleanup_source(db, source_id):
    db.execute(text("DELETE FROM ingestion_sources WHERE id = :id"), {"id": str(source_id)})
    db.commit()


@settings(max_examples=10, deadline=5000)
@given(value=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))))
def test_idempotency(value):
    """Flushing the same value twice leaves exactly one row."""
    db = get_session_factory()()
    sid = uuid4()
    _create_source(db, sid)
    try:
        flush_checkpoint(db, sid, "uid_validity", value)
        flush_checkpoint(db, sid, "uid_validity", value)
        count = db.execute(
            text("SELECT COUNT(*) FROM ingestion_checkpoints WHERE source_id = :sid"),
            {"sid": str(sid)},
        ).fetchone()[0]
        assert count == 1
    finally:
        _cleanup_source(db, sid)
        db.close()


@settings(max_examples=10, deadline=5000)
@given(
    v1=st.integers(min_value=1, max_value=1000),
    v2=st.integers(min_value=1001, max_value=2000),
)
def test_monotonic_cursor(v1, v2):
    """After flushing v2 > v1, load returns v2."""
    db = get_session_factory()()
    sid = uuid4()
    _create_source(db, sid)
    try:
        flush_checkpoint(db, sid, "uid_validity", str(v1))
        flush_checkpoint(db, sid, "uid_validity", str(v2))
        loaded = load_checkpoint(db, sid)
        assert loaded is not None
        assert int(loaded.value) == v2
    finally:
        _cleanup_source(db, sid)
        db.close()


@settings(max_examples=10, deadline=5000)
@given(value=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))))
def test_crash_resume_does_not_skip(value):
    """After flush + simulated crash (new session), load returns the flushed value."""
    db = get_session_factory()()
    sid = uuid4()
    _create_source(db, sid)
    try:
        flush_checkpoint(db, sid, "history_id", value)
        db.close()
        db = get_session_factory()()
        loaded = load_checkpoint(db, sid)
        assert loaded is not None
        assert loaded.value == value
    finally:
        _cleanup_source(db, sid)
        db.close()


@settings(max_examples=10, deadline=5000)
@given(value=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))))
def test_crash_resume_does_not_double_count(value):
    """Flushing same value after crash recovery still results in one row."""
    db = get_session_factory()()
    sid = uuid4()
    _create_source(db, sid)
    try:
        flush_checkpoint(db, sid, "delta_link", value)
        db.close()
        db = get_session_factory()()
        flush_checkpoint(db, sid, "delta_link", value)
        count = db.execute(
            text("SELECT COUNT(*) FROM ingestion_checkpoints WHERE source_id = :sid"),
            {"sid": str(sid)},
        ).fetchone()[0]
        assert count == 1
    finally:
        _cleanup_source(db, sid)
        db.close()
