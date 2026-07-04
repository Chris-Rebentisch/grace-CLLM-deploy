"""Tests for append-only event writer (Chunk 27, D195)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.elicitation.event_writer import (
    DuplicateEventIdError,
    list_events_for_session,
    write_event,
)
from src.elicitation.models import ElicitationEventEnvelope
from src.shared.database import get_session_factory


def _envelope(**overrides):
    base = {
        "event_id": uuid4(),
        "event_type": "session_started",
        "session_id": uuid4(),
        "actor_type": "human",
        "phase_name": "open",
        "emitted_at": datetime.now(timezone.utc),
        "schema_version": 1,
        "grace_version": "0.27.0",
        "payload": {
            "plan_id": None,
            "instrument_selected": None,
            "rationale_string": None,
        },
        "payload_schema_version": 1,
    }
    base.update(overrides)
    return ElicitationEventEnvelope.model_validate(base)


@pytest.fixture(autouse=True)
def clean_elicitation_events():
    factory = get_session_factory()
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()
    yield
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()


def test_write_event_persists_and_list_returns_it_in_order():
    session_factory = get_session_factory()
    session_id = uuid4()
    envelopes = [
        _envelope(session_id=session_id),
        _envelope(session_id=session_id, event_type="phase_entered", payload={
            "entered_phase": "open",
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }),
    ]
    with session_factory() as db:
        for env in envelopes:
            write_event(db, env)
    with session_factory() as db:
        rows = list_events_for_session(db, session_id)
    assert len(rows) == 2
    assert {r["event_type"] for r in rows} == {"session_started", "phase_entered"}


def test_write_event_rejects_duplicate_event_id():
    session_factory = get_session_factory()
    envelope = _envelope()
    with session_factory() as db:
        write_event(db, envelope)
    with session_factory() as db:
        with pytest.raises(DuplicateEventIdError):
            write_event(db, envelope)
