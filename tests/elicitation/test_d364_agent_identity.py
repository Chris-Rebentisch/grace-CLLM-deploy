"""D364 fix-forward regression tests (Chunk 46, D378.a).

Validates: CHECK constraint widening to ``'agent'``, ORM-aligned
agent-identity columns, ``_envelope_to_row()`` extraction, backward
compatibility for ``actor_type='human'`` with NULL agent fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.elicitation.event_writer import write_event
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
        "grace_version": "0.46.0",
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


def test_agent_actor_type_insert_roundtrip():
    """INSERT actor_type='agent' with all three identity fields, verify SELECT."""
    env = _envelope(
        actor_type="agent",
        agent_id="claude-desktop-001",
        agent_display_name="Claude Desktop",
        delegation_source="agent_on_behalf",
    )
    factory = get_session_factory()
    with factory() as db:
        write_event(db, env)
    with factory() as db:
        row = db.execute(
            text(
                "SELECT actor_type, agent_id, agent_display_name, delegation_source "
                "FROM elicitation_events WHERE event_id = :eid"
            ),
            {"eid": env.event_id},
        ).mappings().one()
    assert row["actor_type"] == "agent"
    assert row["agent_id"] == "claude-desktop-001"
    assert row["agent_display_name"] == "Claude Desktop"
    assert row["delegation_source"] == "agent_on_behalf"


def test_human_backward_compatibility():
    """INSERT actor_type='human' with no agent fields — agent columns are NULL."""
    env = _envelope(actor_type="human")
    factory = get_session_factory()
    with factory() as db:
        write_event(db, env)
    with factory() as db:
        row = db.execute(
            text(
                "SELECT actor_type, agent_id, agent_display_name, delegation_source "
                "FROM elicitation_events WHERE event_id = :eid"
            ),
            {"eid": env.event_id},
        ).mappings().one()
    assert row["actor_type"] == "human"
    assert row["agent_id"] is None
    assert row["agent_display_name"] is None
    assert row["delegation_source"] is None


def test_migration_downgrade_upgrade_roundtrip():
    """Downgrade c46a reverts CHECK to ('human', 'system'), upgrade re-applies."""
    from alembic.config import Config
    from alembic.command import downgrade, upgrade

    cfg = Config("alembic.ini")
    downgrade(cfg, "c45a_support_sessions")
    factory = get_session_factory()
    with factory() as db:
        constraint_def = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conname = 'ck_elicitation_events_actor_type'"
            )
        ).scalar()
    assert "'agent'" not in constraint_def

    upgrade(cfg, "head")
    with factory() as db:
        constraint_def = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conname = 'ck_elicitation_events_actor_type'"
            )
        ).scalar()
    assert "'agent'" in constraint_def


def test_null_agent_fields_accepted_for_human():
    """Explicit None for agent fields accepted when actor_type='human'."""
    env = _envelope(
        actor_type="human",
        agent_id=None,
        agent_display_name=None,
        delegation_source=None,
    )
    factory = get_session_factory()
    with factory() as db:
        received = write_event(db, env)
    assert received is not None


def test_invalid_actor_type_rejected():
    """CHECK constraint rejects actor_type not in ('human', 'system', 'agent')."""
    factory = get_session_factory()
    with factory() as db:
        with pytest.raises(Exception):
            db.execute(
                text(
                    "INSERT INTO elicitation_events "
                    "(event_id, event_type, session_id, actor_type, phase_name, "
                    "emitted_at, schema_version, grace_version, payload, "
                    "payload_schema_version) VALUES "
                    "(:eid, 'session_started', :sid, 'robot', 'open', "
                    "now(), 1, '0.46.0', '{}'::jsonb, 1)"
                ),
                {"eid": str(uuid4()), "sid": str(uuid4())},
            )
