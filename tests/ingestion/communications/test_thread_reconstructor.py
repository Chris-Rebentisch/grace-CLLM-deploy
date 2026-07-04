"""Tests for thread_reconstructor.py (Chunk 80a, D513).

Unit and integration tests for RFC 5322 thread reconstruction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.ingestion.communications.thread_reconstructor import (
    _normalize_subject,
    _recipients_hash,
    reconstruct_threads,
)
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
        {"id": source_id, "name": f"test-thread-{source_id}"},
    )
    db_session.commit()
    yield source_id
    db_session.execute(
        text("DELETE FROM ingestion_sources WHERE id = :id"),
        {"id": source_id},
    )
    db_session.commit()


def _insert_event(db_session, source_id, **kwargs):
    """Insert a communication_events row with defaults."""
    row_id = kwargs.get("id", uuid4())
    db_session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, source_id, "
            " triage_tier_outcome, in_reply_to, references_json, subject, sent_at) "
            "VALUES (:id, :message_id, :sender, CAST(:recipients AS jsonb), :source_id, "
            " 'pending', :in_reply_to, CAST(:references_json AS jsonb), :subject, :sent_at)"
        ),
        {
            "id": row_id,
            "message_id": kwargs.get("message_id", f"<{row_id}@example.com>"),
            "sender": kwargs.get("sender_email", "test@example.com"),
            "recipients": kwargs.get("recipients", '["to@example.com"]'),
            "source_id": source_id,
            "in_reply_to": kwargs.get("in_reply_to"),
            "references_json": kwargs.get("references_json"),
            "subject": kwargs.get("subject", "Test"),
            "sent_at": kwargs.get("sent_at"),
        },
    )
    return row_id


def _cleanup_events(db_session, event_ids):
    """Delete test events using alembic bypass."""
    for eid in event_ids:
        db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        db_session.execute(
            text("DELETE FROM communication_events WHERE id = :id"),
            {"id": eid},
        )
    db_session.commit()


def test_dag_build_from_references(db_session, test_source):
    """References chain parsed into correct DAG with thread_id assignment."""
    ids = []
    try:
        # Root message
        root_msg = "<root@example.com>"
        r1 = _insert_event(
            db_session, test_source,
            message_id=root_msg,
            sent_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        # Reply referencing root
        reply_msg = "<reply1@example.com>"
        r2 = _insert_event(
            db_session, test_source,
            message_id=reply_msg,
            in_reply_to=root_msg,
            references_json=f'["{root_msg}"]',
            sent_at=datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc),
        )
        ids.append(r2)

        db_session.commit()

        result = reconstruct_threads(db_session, source_id=test_source, reprocess=True)
        assert result["event_count"] >= 2

        # Verify both events have the same thread_id = root message
        row = db_session.execute(
            text("SELECT thread_id, thread_position FROM communication_events WHERE id = :id"),
            {"id": r1},
        ).fetchone()
        assert row.thread_id == root_msg
        assert row.thread_position == 0

        row2 = db_session.execute(
            text("SELECT thread_id, thread_position FROM communication_events WHERE id = :id"),
            {"id": r2},
        ).fetchone()
        assert row2.thread_id == root_msg
        assert row2.thread_position == 1
    finally:
        _cleanup_events(db_session, ids)


def test_jwz_robustness_rule(db_session, test_source):
    """In-Reply-To not in References gets appended (JWZ robustness)."""
    ids = []
    try:
        root_msg = "<jwz-root@example.com>"
        other_msg = "<jwz-other@example.com>"
        reply_msg = "<jwz-reply@example.com>"

        r1 = _insert_event(
            db_session, test_source,
            message_id=root_msg,
            sent_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        # Reply with In-Reply-To pointing to other_msg, but References has only root
        r2 = _insert_event(
            db_session, test_source,
            message_id=reply_msg,
            in_reply_to=other_msg,
            references_json=f'["{root_msg}"]',
            sent_at=datetime(2026, 1, 2, 11, 0, tzinfo=timezone.utc),
        )
        ids.append(r2)

        db_session.commit()

        result = reconstruct_threads(db_session, source_id=test_source, reprocess=True)
        assert result["event_count"] >= 1

        # The reply should be in the root's thread
        row = db_session.execute(
            text("SELECT thread_id FROM communication_events WHERE id = :id"),
            {"id": r2},
        ).fetchone()
        assert row.thread_id == root_msg
    finally:
        _cleanup_events(db_session, ids)


def test_orphan_flag(db_session, test_source):
    """Absent parent sets thread_orphan=true."""
    ids = []
    try:
        orphan_msg = "<orphan@example.com>"
        missing_parent = "<missing-parent@example.com>"

        r1 = _insert_event(
            db_session, test_source,
            message_id=orphan_msg,
            in_reply_to=missing_parent,
            references_json=f'["{missing_parent}"]',
            sent_at=datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        db_session.commit()

        reconstruct_threads(db_session, source_id=test_source, reprocess=True)

        row = db_session.execute(
            text("SELECT thread_orphan FROM communication_events WHERE id = :id"),
            {"id": r1},
        ).fetchone()
        assert row.thread_orphan is True
    finally:
        _cleanup_events(db_session, ids)


def test_position_assignment_root_zero(db_session, test_source):
    """Root message gets thread_position=0."""
    ids = []
    try:
        root_msg = "<pos-root@example.com>"
        r1 = _insert_event(
            db_session, test_source,
            message_id=root_msg,
            in_reply_to=None,
            references_json=None,
            sent_at=datetime(2026, 1, 4, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        db_session.commit()

        reconstruct_threads(db_session, source_id=test_source, reprocess=True)

        row = db_session.execute(
            text("SELECT thread_position FROM communication_events WHERE id = :id"),
            {"id": r1},
        ).fetchone()
        assert row.thread_position == 0
    finally:
        _cleanup_events(db_session, ids)


def test_proxy_grouping(db_session, test_source):
    """Weak-header messages grouped by subject+participants proxy."""
    ids = []
    try:
        # Two messages with same subject and recipients but no References/In-Reply-To
        r1 = _insert_event(
            db_session, test_source,
            message_id="<proxy1@example.com>",
            subject="Re: Budget discussion",
            recipients='["finance@company.com"]',
            sent_at=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        r2 = _insert_event(
            db_session, test_source,
            message_id="<proxy2@example.com>",
            subject="Budget discussion",
            recipients='["finance@company.com"]',
            sent_at=datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc),
        )
        ids.append(r2)

        db_session.commit()

        result = reconstruct_threads(db_session, source_id=test_source, reprocess=True)

        # Both should be in the same proxy group with thread_orphan=True
        row1 = db_session.execute(
            text("SELECT thread_id, thread_orphan, thread_position FROM communication_events WHERE id = :id"),
            {"id": r1},
        ).fetchone()
        row2 = db_session.execute(
            text("SELECT thread_id, thread_orphan, thread_position FROM communication_events WHERE id = :id"),
            {"id": r2},
        ).fetchone()

        # Same thread_id
        assert row1.thread_id == row2.thread_id
        # Both marked as orphan (proxy fallback)
        assert row1.thread_orphan is True
        assert row2.thread_orphan is True
        # Positions assigned
        assert row1.thread_position is not None
        assert row2.thread_position is not None
    finally:
        _cleanup_events(db_session, ids)


def test_reconstruct_integration_roundtrip(db_session, test_source):
    """End-to-end: reconstruct → verify CE rows have thread_id and thread_position."""
    ids = []
    try:
        root_msg = "<rt-root@example.com>"
        reply_msg = "<rt-reply@example.com>"

        r1 = _insert_event(
            db_session, test_source,
            message_id=root_msg,
            sent_at=datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)

        r2 = _insert_event(
            db_session, test_source,
            message_id=reply_msg,
            in_reply_to=root_msg,
            references_json=f'["{root_msg}"]',
            sent_at=datetime(2026, 1, 6, 11, 0, tzinfo=timezone.utc),
        )
        ids.append(r2)

        db_session.commit()

        result = reconstruct_threads(db_session, source_id=test_source, reprocess=True)
        assert result["thread_count"] >= 1
        assert result["event_count"] >= 2

        # Verify populated fields
        for eid in ids:
            row = db_session.execute(
                text("SELECT thread_id, thread_position FROM communication_events WHERE id = :id"),
                {"id": eid},
            ).fetchone()
            assert row.thread_id is not None
            assert row.thread_position is not None
    finally:
        _cleanup_events(db_session, ids)


def test_reconstruct_invokes_supersession_pass(db_session, test_source):
    """CP3 step 5 — reconstructor calls supersession after Postgres commit."""
    ids = []
    try:
        root_msg = "<sup-root@example.com>"
        r1 = _insert_event(
            db_session, test_source,
            message_id=root_msg,
            sent_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        )
        ids.append(r1)
        db_session.commit()

        with patch(
            "src.ingestion.communications.thread_reconstructor._apply_supersession_for_threads"
        ) as mock_supersede:
            reconstruct_threads(db_session, source_id=test_source, reprocess=True)
            mock_supersede.assert_called_once()
    finally:
        _cleanup_events(db_session, ids)
