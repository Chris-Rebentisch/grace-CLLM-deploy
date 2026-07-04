"""SQLAlchemy table definition for `elicitation_events` (protocol §8.3).

Append-only at the DB layer via trigger (see the Alembic migration). The
Python object here is the read/write surface used by `event_writer.py`.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

elicitation_events = Table(
    "elicitation_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_type", String(64), nullable=False),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("actor_type", String(16), nullable=False),
    Column("phase_name", String(16), nullable=False),
    Column("emitted_at", DateTime(timezone=True), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("grace_version", String(32), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_schema_version", Integer, nullable=False),
    Column(
        "received_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    # D364 / D378.a — agent identity columns (DB-side from c44a; ORM-aligned
    # by Chunk 46).  Invariant: c44a added the columns; c46a widened the
    # CHECK constraint.  Authorization: D378.a, spec §6 CP1.
    Column("agent_id", Text, nullable=True),
    Column("agent_display_name", Text, nullable=True),
    Column("delegation_source", Text, nullable=True),
    CheckConstraint(
        "actor_type IN ('human', 'system', 'agent')",
        name="ck_elicitation_events_actor_type",
    ),
    Index("ix_elicitation_events_session_id", "session_id"),
    Index("ix_elicitation_events_phase_name", "phase_name"),
    Index("ix_elicitation_events_event_type", "event_type"),
)
