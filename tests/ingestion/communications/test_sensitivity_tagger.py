"""Tests for the sensitivity tagger (Chunk 59, D426/D439/D440, CP3+CP4+CP5).

CP3: 6 privilege-detection tests
CP4: 9 PII-density + external-boundary + serialization tests
CP5: 6 thread-level propagation tests
Total: 21
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.ingestion.communications.sensitivity_tagger import (
    _detect_external_boundary,
    _detect_pii_dense,
    _detect_privileged,
    _propagate_thread_tags,
    tags_from_bar_form,
    tags_to_bar_form,
)
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
        {"id": str(sid), "name": f"sens-src-{sid}"},
    )
    session.commit()
    return str(sid)


def _insert_event(session, source_id: str, **overrides) -> str:
    eid = uuid4()
    defaults = {
        "id": str(eid),
        "mid": f"msg-{eid}",
        "email": "alice@internal.com",
        "rj": '[{"email": "bob@internal.com", "role": "to"}]',
        "outcome": "pending",
        "sid": source_id,
        "subject": None,
        "body_plain": None,
        "thread_id": None,
    }
    defaults.update(overrides)
    session.execute(
        text(
            "INSERT INTO communication_events "
            "(id, message_id, sender_email, recipients_json, "
            "triage_tier_outcome, source_id, subject, body_plain, thread_id) "
            "VALUES (:id, :mid, :email, :rj, :outcome, :sid, :subject, :body_plain, :thread_id)"
        ),
        defaults,
    )
    session.commit()
    return defaults["id"]


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
# CP3: Privilege detection (6 tests)
# =========================================================================

class TestPrivilegeDetection:
    """Privilege detection tests (CP3)."""

    def test_legal_counsel_recipient_fires_privileged(self):
        """privileged fires on legal_counsel recipient match."""
        event = {"body_plain": "Hello", "subject": "Re: matter"}
        categories = {"lawyer@firm.com": "legal_counsel"}
        tags = _detect_privileged(event, {}, recipient_categories=categories)
        assert "privileged" in tags

    def test_privilege_phrase_fires_privileged(self):
        """privileged fires on each privilege phrase match."""
        config = {"privilege_phrases": ["privileged and confidential"]}
        event = {
            "body_plain": "Privileged and Confidential\nDear counsel,",
            "subject": "Legal matter",
        }
        tags = _detect_privileged(event, config)
        assert "privileged" in tags

    def test_line_start_anchor_rejects_forward_quoted(self):
        """Line-start anchor rejects forward-quoted lines."""
        config = {"privilege_phrases": ["privileged and confidential"]}
        event = {
            "body_plain": "> Privileged and Confidential\nActual content here",
            "subject": "Fwd: Legal",
        }
        tags = _detect_privileged(event, config)
        assert "privileged" not in tags

    def test_waived_coappends_multi_recipient(self):
        """privilege_potentially_waived co-appends under multi-recipient condition."""
        categories = {
            "lawyer@firm.com": "legal_counsel",
            "outsider@external.com": "vendor",
        }
        event = {"body_plain": "Hello", "subject": ""}
        tags = _detect_privileged(event, {}, recipient_categories=categories)
        assert "privileged" in tags
        assert "privilege_potentially_waived" in tags

    def test_empty_config_degrades_to_recipient_only(self):
        """Empty config → only recipient-based detection (trigger a)."""
        categories = {"lawyer@firm.com": "legal_counsel"}
        event = {"body_plain": "Normal email", "subject": "Hi"}
        tags = _detect_privileged(event, {}, recipient_categories=categories)
        assert "privileged" in tags

    def test_non_legal_counsel_no_privileged(self):
        """Non-legal_counsel-only email does NOT produce privileged via trigger (a)."""
        categories = {"colleague@internal.com": "peer"}
        event = {"body_plain": "Normal email", "subject": "Hi"}
        tags = _detect_privileged(event, {}, recipient_categories=categories)
        assert "privileged" not in tags


# =========================================================================
# CP4: PII-density + external-boundary + serialization (9 tests)
# =========================================================================

class TestPiiDensity:
    """PII-density detection tests."""

    def test_fires_at_threshold(self):
        """pii_dense fires at/above threshold."""
        # 10 words, need >= 0.5 PII tokens for 5.0 threshold
        body = "Call John Smith at 123-45-6789 please"
        event = {"body_plain": body}
        config = {"pii_density_threshold": 5.0}
        tags = _detect_pii_dense(event, config)
        assert "pii_dense" in tags

    def test_below_threshold_no_fire(self):
        """Below-threshold does not fire."""
        body = "The quick brown fox jumps over the lazy dog " * 10
        event = {"body_plain": body}
        config = {"pii_density_threshold": 5.0}
        tags = _detect_pii_dense(event, config)
        assert "pii_dense" not in tags

    def test_empty_body_no_fire(self):
        """Empty / NULL body does not fire."""
        for body in [None, "", None]:
            event = {"body_plain": body, "body_html": None}
            tags = _detect_pii_dense(event, {})
            assert tags == []


class TestExternalBoundary:
    """External-boundary detection tests."""

    def test_fires_on_external_domain(self):
        """external_boundary fires on external domain in From/To/Cc."""
        event = {
            "sender_email": "alice@external.com",
            "recipients_json": [{"email": "bob@internal.com", "role": "to"}],
        }
        tags = _detect_external_boundary(event, ["internal.com"])
        assert "external_boundary" in tags

    def test_internal_only_no_fire(self):
        """Internal-only does not fire."""
        event = {
            "sender_email": "alice@internal.com",
            "recipients_json": [{"email": "bob@internal.com", "role": "to"}],
        }
        tags = _detect_external_boundary(event, ["internal.com"])
        assert "external_boundary" not in tags

    def test_empty_org_domains_skips(self):
        """Empty organization_domains emits warning + skips."""
        event = {
            "sender_email": "alice@external.com",
            "recipients_json": [],
        }
        tags = _detect_external_boundary(event, [])
        assert tags == []


class TestBarFormSerialization:
    """Bar-form serialization helper tests."""

    def test_round_trip(self):
        """Bar-form helpers round-trip correctly."""
        original = ["external_boundary", "privileged"]
        bar = tags_to_bar_form(original)
        assert bar == "|external_boundary|privileged|"
        restored = tags_from_bar_form(bar)
        assert restored == ["external_boundary", "privileged"]

    def test_pipe_in_tag_raises(self):
        """Tag containing | raises ValueError."""
        with pytest.raises(ValueError, match="must not contain"):
            tags_to_bar_form(["bad|tag"])

    def test_alphabetic_sort_deterministic(self):
        """Alphabetic sort is deterministic."""
        tags1 = tags_to_bar_form(["privileged", "external_boundary", "pii_dense"])
        tags2 = tags_to_bar_form(["pii_dense", "privileged", "external_boundary"])
        assert tags1 == tags2
        assert tags1 == "|external_boundary|pii_dense|privileged|"


# =========================================================================
# CP5: Thread-level propagation (6 tests)
# =========================================================================

class TestThreadPropagation:
    """Thread-level sensitivity propagation tests."""

    def test_upsert_alphabetic_union(self, db_session):
        """UPSERT produces alphabetic-sorted bar-form union."""
        sid = _ensure_source(db_session)
        tid = f"thread-{uuid4()}"
        try:
            _insert_event(db_session, sid, thread_id=tid, body_plain="Call 123-45-6789 " * 5)
            # Manually set sensitivity_tags on the event
            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = :tags "
                    "WHERE thread_id = :tid"
                ),
                {"tags": "|pii_dense|privileged|", "tid": tid},
            )
            db_session.commit()

            _propagate_thread_tags(db_session, tid, "msg-x")
            db_session.commit()

            row = db_session.execute(
                text(
                    "SELECT propagated_tags FROM communication_sensitivity_propagation "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid},
            ).fetchone()
            assert row is not None
            tags = tags_from_bar_form(row[0])
            assert tags == ["pii_dense", "privileged"]
        finally:
            _cleanup(db_session, sid)

    def test_untag_repass_removes(self, db_session):
        """Un-tag re-pass removes dropped value."""
        sid = _ensure_source(db_session)
        tid = f"thread-{uuid4()}"
        try:
            eid = _insert_event(db_session, sid, thread_id=tid)
            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = :tags "
                    "WHERE id = :id"
                ),
                {"tags": "|pii_dense|privileged|", "id": eid},
            )
            db_session.commit()
            _propagate_thread_tags(db_session, tid, "msg-x")
            db_session.commit()

            # Now remove privileged
            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = :tags "
                    "WHERE id = :id"
                ),
                {"tags": "|pii_dense|", "id": eid},
            )
            db_session.commit()
            _propagate_thread_tags(db_session, tid, "msg-x")
            db_session.commit()

            row = db_session.execute(
                text(
                    "SELECT propagated_tags FROM communication_sensitivity_propagation "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid},
            ).fetchone()
            tags = tags_from_bar_form(row[0])
            assert "privileged" not in tags
            assert "pii_dense" in tags
        finally:
            _cleanup(db_session, sid)

    def test_propagation_trigger_rejects_delete(self, db_session):
        """Propagation trigger rejects DELETE."""
        tid = f"thread-{uuid4()}"
        db_session.execute(
            text(
                "INSERT INTO communication_sensitivity_propagation "
                "(thread_id, propagated_tags) VALUES (:tid, '')"
            ),
            {"tid": tid},
        )
        db_session.commit()
        try:
            with pytest.raises(Exception, match="append-only"):
                db_session.execute(
                    text(
                        "DELETE FROM communication_sensitivity_propagation "
                        "WHERE thread_id = :tid"
                    ),
                    {"tid": tid},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            db_session.execute(
                text(
                    "DELETE FROM communication_sensitivity_propagation WHERE thread_id = :tid"
                ),
                {"tid": tid},
            )
            db_session.commit()

    def test_propagation_admits_mutable_only(self, db_session):
        """UPDATE admitted on propagated_tags + last_recomputed_at only."""
        tid = f"thread-{uuid4()}"
        db_session.execute(
            text(
                "INSERT INTO communication_sensitivity_propagation "
                "(thread_id, propagated_tags) VALUES (:tid, '')"
            ),
            {"tid": tid},
        )
        db_session.commit()

        # Mutable update OK
        db_session.execute(
            text(
                "UPDATE communication_sensitivity_propagation SET "
                "propagated_tags = '|privileged|', last_recomputed_at = NOW() "
                "WHERE thread_id = :tid"
            ),
            {"tid": tid},
        )
        db_session.commit()

        # Immutable update blocked
        try:
            with pytest.raises(Exception, match="only propagated_tags"):
                db_session.execute(
                    text(
                        "UPDATE communication_sensitivity_propagation SET "
                        "propagated_at = '2020-01-01'::timestamptz "
                        "WHERE thread_id = :tid"
                    ),
                    {"tid": tid},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
            db_session.execute(
                text(
                    "DELETE FROM communication_sensitivity_propagation WHERE thread_id = :tid"
                ),
                {"tid": tid},
            )
            db_session.commit()

    def test_orphan_thread_propagates_singleton(self, db_session):
        """Orphan thread (NULL thread_id) propagates singleton correctly."""
        sid = _ensure_source(db_session)
        try:
            eid = _insert_event(db_session, sid, thread_id=None)
            mid = db_session.execute(
                text("SELECT message_id FROM communication_events WHERE id = :id"),
                {"id": eid},
            ).fetchone()[0]

            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = '|privileged|' "
                    "WHERE id = :id"
                ),
                {"id": eid},
            )
            db_session.commit()

            _propagate_thread_tags(db_session, None, mid)
            db_session.commit()

            row = db_session.execute(
                text(
                    "SELECT propagated_tags FROM communication_sensitivity_propagation "
                    "WHERE thread_id = :tid"
                ),
                {"tid": mid},
            ).fetchone()
            assert row is not None
            assert "privileged" in tags_from_bar_form(row[0])
        finally:
            _cleanup(db_session, sid)

    def test_multi_thread_independent(self, db_session):
        """Multi-thread propagation is independent."""
        sid = _ensure_source(db_session)
        tid_a = f"thread-{uuid4()}"
        tid_b = f"thread-{uuid4()}"
        try:
            _insert_event(db_session, sid, thread_id=tid_a)
            _insert_event(db_session, sid, thread_id=tid_b)
            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = '|privileged|' "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid_a},
            )
            db_session.execute(
                text(
                    "UPDATE communication_events SET sensitivity_tags = '|pii_dense|' "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid_b},
            )
            db_session.commit()

            _propagate_thread_tags(db_session, tid_a, "msg-a")
            _propagate_thread_tags(db_session, tid_b, "msg-b")
            db_session.commit()

            row_a = db_session.execute(
                text(
                    "SELECT propagated_tags FROM communication_sensitivity_propagation "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid_a},
            ).fetchone()
            row_b = db_session.execute(
                text(
                    "SELECT propagated_tags FROM communication_sensitivity_propagation "
                    "WHERE thread_id = :tid"
                ),
                {"tid": tid_b},
            ).fetchone()

            assert "privileged" in tags_from_bar_form(row_a[0])
            assert "pii_dense" not in tags_from_bar_form(row_a[0])
            assert "pii_dense" in tags_from_bar_form(row_b[0])
            assert "privileged" not in tags_from_bar_form(row_b[0])
        finally:
            _cleanup(db_session, sid)
