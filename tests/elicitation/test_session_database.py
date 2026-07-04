"""Tests for elicitation_sessions table CRUD + append-only constraints (D223).

CP1 verification: 4 tests covering CRUD round-trip, append-only DELETE
blocked, immutable-column UPDATE blocked, and JSONB session_plan_jsonb
round-trip with nested structure.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from src.elicitation.session_database import (
    ElicitationSessionRow,
    close_session,
    create_session,
    get_session,
    update_phase,
)
from src.shared.database import get_db


def _get_db():
    """Get a database session for testing."""
    gen = get_db()
    db = next(gen)
    return db, gen


def _close_db(gen):
    try:
        next(gen)
    except StopIteration:
        pass


class TestElicitationSessionDatabase:
    """CP1 verification tests."""

    def test_crud_round_trip(self):
        """Create -> fetch -> assert equality on columnar + JSONB payload."""
        db, gen = _get_db()
        try:
            sid = uuid4()
            plan = {"phases": ["open", "structure"], "instruments": ["laddering"]}
            row = create_session(
                db,
                session_id=sid,
                actor_type="human",
                current_phase="open",
                session_plan=plan,
            )
            assert row.session_id == sid
            assert row.actor_type == "human"
            assert row.current_phase == "open"
            assert row.started_at is not None
            assert row.session_plan_jsonb == plan

            # Fetch
            fetched = get_session(db, sid)
            assert fetched is not None
            assert fetched.session_id == sid
            assert fetched.session_plan_jsonb == plan

            # Update phase
            updated = update_phase(db, sid, "structure")
            assert updated is not None
            assert updated.current_phase == "structure"

            # Close
            closed = close_session(db, sid)
            assert closed is not None
            assert closed.current_phase == "close"
            assert closed.closed_at is not None
        finally:
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(
                text("DELETE FROM elicitation_sessions WHERE session_id = :sid"),
                {"sid": str(sid)},
            )
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_append_only_delete_blocked(self):
        """Trigger blocks DELETE on elicitation_sessions rows."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            create_session(db, session_id=sid, session_plan={})
            with pytest.raises(Exception) as excinfo:
                db.execute(
                    text("DELETE FROM elicitation_sessions WHERE session_id = :sid"),
                    {"sid": str(sid)},
                )
                db.commit()
            db.rollback()
            msg = str(excinfo.value).lower()
            assert (
                "append-only" in msg
                or "delete forbidden" in msg
                or "delete is not permitted" in msg
            )
        finally:
            # Force cleanup by disabling the trigger temporarily
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(
                text("DELETE FROM elicitation_sessions WHERE session_id = :sid"),
                {"sid": str(sid)},
            )
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_immutable_column_update_blocked(self):
        """Trigger blocks UPDATE on immutable columns (session_id, actor_type, started_at)."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            create_session(db, session_id=sid, actor_type="human", session_plan={})
            with pytest.raises(Exception) as excinfo:
                db.execute(
                    text(
                        "UPDATE elicitation_sessions SET actor_type = 'system' "
                        "WHERE session_id = :sid"
                    ),
                    {"sid": str(sid)},
                )
                db.commit()
            db.rollback()
            assert "immutable" in str(excinfo.value).lower() or "cannot be modified" in str(excinfo.value)
        finally:
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(
                text("DELETE FROM elicitation_sessions WHERE session_id = :sid"),
                {"sid": str(sid)},
            )
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_jsonb_session_plan_round_trip(self):
        """JSONB session_plan_jsonb round-trips nested Pydantic-like structure."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            nested_plan = {
                "plan_id": str(uuid4()),
                "phases": [
                    {
                        "name": "open",
                        "instruments": [
                            {"type": "laddering", "config": {"depth": 3}},
                        ],
                    },
                    {
                        "name": "structure",
                        "instruments": [
                            {"type": "card_sort", "config": {"categories": 5}},
                            {"type": "teach_back", "config": {"sentences": 10}},
                        ],
                    },
                ],
                "metadata": {"created_by": "test", "version": 1},
            }
            row = create_session(db, session_id=sid, session_plan=nested_plan)
            assert row.session_plan_jsonb == nested_plan

            fetched = get_session(db, sid)
            assert fetched is not None
            assert fetched.session_plan_jsonb == nested_plan
            assert fetched.session_plan_jsonb["phases"][1]["instruments"][0]["type"] == "card_sort"
        finally:
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(
                text("DELETE FROM elicitation_sessions WHERE session_id = :sid"),
                {"sid": str(sid)},
            )
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)
