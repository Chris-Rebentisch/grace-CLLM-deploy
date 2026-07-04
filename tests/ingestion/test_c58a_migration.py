"""Tests for c58a_voice_tone_profiles migration (Chunk 58, CP2).

Validates:
1. Append-only trigger raises on direct DELETE
2. SECURITY DEFINER prune succeeds and removes oldest
3. Bypass variable does not leak outside function transaction
4. Recipient append-only trigger
5. Mutual-exclusion CHECK constraint
6. Department VIEW filters aggregate only
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory


@pytest.fixture
def db_session():
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        # Clean up test data via bypass in a fresh transaction
        try:
            session.execute(text("SET LOCAL app.voice_tone_prune = 'true'"))
            session.execute(text("DELETE FROM recipient_style_profiles WHERE TRUE"))
            session.execute(text("DELETE FROM communication_style_profiles WHERE TRUE"))
            session.commit()
        except Exception:
            session.rollback()
        session.close()


def _insert_profile(session, sender_id=None, aggregate_segment=None, version=1):
    """Insert a communication_style_profiles row and return its id."""
    pid = uuid4()
    session.execute(
        text(
            "INSERT INTO communication_style_profiles"
            " (id, sender_person_id, aggregate_segment, profile_version,"
            "  style_signature, profile_quality_band)"
            " VALUES"
            " (:id, :sender_id, :agg_seg, :version,"
            "  cast(:sig as jsonb), :band)"
        ),
        {
            "id": str(pid),
            "sender_id": str(sender_id) if sender_id else None,
            "agg_seg": aggregate_segment,
            "version": version,
            "sig": '{"sentence_length_band":"medium"}',
            "band": "medium",
        },
    )
    session.commit()
    return pid


class TestC58aMigration:
    """Tests for c58a_voice_tone_profiles migration."""

    def test_append_only_trigger_blocks_delete(self, db_session):
        """Direct DELETE on communication_style_profiles raises check_violation."""
        sender_id = uuid4()
        pid = _insert_profile(db_session, sender_id=sender_id)

        with pytest.raises(Exception, match="append-only"):
            db_session.execute(
                text("DELETE FROM communication_style_profiles WHERE id = :id"),
                {"id": str(pid)},
            )

    def test_security_definer_prune_succeeds(self, db_session):
        """SECURITY DEFINER prune_voice_tone_versions removes oldest versions."""
        sender_id = uuid4()
        for v in range(1, 6):
            _insert_profile(db_session, sender_id=sender_id, version=v)

        result = db_session.execute(
            text("SELECT prune_voice_tone_versions(:sid, NULL, 2)"),
            {"sid": str(sender_id)},
        )
        deleted_count = result.scalar()
        db_session.commit()
        assert deleted_count == 3

        remaining = db_session.execute(
            text(
                "SELECT profile_version FROM communication_style_profiles"
                " WHERE sender_person_id = :sid"
                " ORDER BY profile_version"
            ),
            {"sid": str(sender_id)},
        ).fetchall()
        assert [r[0] for r in remaining] == [4, 5]

    def test_bypass_variable_does_not_leak(self, db_session):
        """app.voice_tone_prune resets after prune — direct DELETE still fails."""
        sender_id = uuid4()
        _insert_profile(db_session, sender_id=sender_id, version=1)
        _insert_profile(db_session, sender_id=sender_id, version=2)

        db_session.execute(
            text("SELECT prune_voice_tone_versions(:sid, NULL, 1)"),
            {"sid": str(sender_id)},
        )
        db_session.commit()

        _insert_profile(db_session, sender_id=sender_id, version=3)
        with pytest.raises(Exception, match="append-only"):
            db_session.execute(
                text(
                    "DELETE FROM communication_style_profiles"
                    " WHERE sender_person_id = :sid AND profile_version = 3"
                ),
                {"sid": str(sender_id)},
            )

    def test_recipient_append_only_trigger(self, db_session):
        """Direct DELETE on recipient_style_profiles raises check_violation."""
        sender_id = uuid4()
        pid = _insert_profile(db_session, sender_id=sender_id)

        recipient_id = uuid4()
        rid = uuid4()
        db_session.execute(
            text(
                "INSERT INTO recipient_style_profiles"
                " (id, profile_id, recipient_person_id, category,"
                "  confidence_band, style_delta)"
                " VALUES"
                " (:id, :pid, :rid, 'peer_same_department', 'medium',"
                "  cast(:delta as jsonb))"
            ),
            {"id": str(rid), "pid": str(pid), "rid": str(recipient_id), "delta": "{}"},
        )
        db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            db_session.execute(
                text("DELETE FROM recipient_style_profiles WHERE id = :id"),
                {"id": str(rid)},
            )

    def test_mutual_exclusion_check(self, db_session):
        """Cannot insert with both sender_person_id and aggregate_segment set."""
        with pytest.raises(Exception):
            db_session.execute(
                text(
                    "INSERT INTO communication_style_profiles"
                    " (id, sender_person_id, aggregate_segment, profile_version,"
                    "  style_signature, profile_quality_band)"
                    " VALUES"
                    " (:id, :sid, 'engineering', 1, cast(:sig as jsonb), 'medium')"
                ),
                {
                    "id": str(uuid4()),
                    "sid": str(uuid4()),
                    "sig": "{}",
                },
            )

    def test_department_view_filters_aggregate(self, db_session):
        """department_communication_profiles VIEW shows only aggregate rows."""
        sender_id = uuid4()
        _insert_profile(db_session, sender_id=sender_id, version=1)
        _insert_profile(db_session, aggregate_segment="engineering", version=1)

        rows = db_session.execute(
            text("SELECT * FROM department_communication_profiles")
        ).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row[1] is None  # sender_person_id
            assert row[2] is not None  # aggregate_segment
