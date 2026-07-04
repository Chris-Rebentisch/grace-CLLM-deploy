"""Append-only writer for `elicitation_events` (Chunk 27, D195).

The writer exposes INSERT-only methods plus read helpers. The underlying
table also has a PostgreSQL trigger blocking UPDATE/DELETE — the
application-layer and DB-layer constraints are belt-and-suspenders. No
UPDATE or DELETE methods exist here by design (D195).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.elicitation.models import ElicitationEventEnvelope

logger = structlog.get_logger()


class DuplicateEventIdError(Exception):
    """Raised when an event with the same event_id is written twice."""


def _envelope_to_row(envelope: ElicitationEventEnvelope) -> dict[str, Any]:
    row = {
        "event_id": envelope.event_id,
        "event_type": envelope.event_type,
        "session_id": envelope.session_id,
        "actor_type": envelope.actor_type,
        "phase_name": envelope.phase_name,
        "emitted_at": envelope.emitted_at,
        "schema_version": envelope.schema_version,
        "grace_version": envelope.grace_version,
        "payload": envelope.payload,
        "payload_schema_version": envelope.payload_schema_version,
    }
    # D378.a / D364 fix-forward — extract agent-identity fields when present.
    # Pre-existing envelopes with None agent fields produce NULL columns
    # (backward compatible).  Invariant: c44a added these columns; c46a
    # widened the CHECK constraint to accept actor_type='agent'.
    # Authorization: D378.a, spec §6 CP1.
    agent_id = getattr(envelope, "agent_id", None)
    agent_display_name = getattr(envelope, "agent_display_name", None)
    delegation_source = getattr(envelope, "delegation_source", None)
    if agent_id is not None:
        row["agent_id"] = agent_id
    if agent_display_name is not None:
        row["agent_display_name"] = agent_display_name
    if delegation_source is not None:
        row["delegation_source"] = delegation_source
    return row


def write_event(
    session: Session, envelope: ElicitationEventEnvelope
) -> datetime:
    """Write one event. Returns the `received_at` the DB assigned."""
    from src.elicitation.schema import elicitation_events

    row = _envelope_to_row(envelope)
    try:
        result = session.execute(
            insert(elicitation_events)
            .values(**row)
            .returning(elicitation_events.c.received_at)
        )
        received_at = result.scalar_one()
        session.commit()
    except IntegrityError as err:
        session.rollback()
        if "duplicate key" in str(err.orig).lower() or "unique" in str(err.orig).lower():
            raise DuplicateEventIdError(
                f"event_id {envelope.event_id} already stored"
            ) from err
        raise
    logger.info(
        "elicitation.event_written",
        event_id=str(envelope.event_id),
        event_type=envelope.event_type,
        session_id=str(envelope.session_id),
        phase_name=envelope.phase_name,
    )
    return received_at


def list_events_for_session(
    session: Session, session_id: UUID, limit: int = 500
) -> list[dict[str, Any]]:
    from src.elicitation.schema import elicitation_events

    stmt = (
        select(elicitation_events)
        .where(elicitation_events.c.session_id == session_id)
        .order_by(elicitation_events.c.emitted_at.asc())
        .limit(limit)
    )
    return [dict(row._mapping) for row in session.execute(stmt)]


def count_events(session: Session) -> int:
    from src.elicitation.schema import elicitation_events

    return session.scalar(
        select(elicitation_events).with_only_columns(
            elicitation_events.c.event_id
        ).count()
    ) or 0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Intentionally no update_event / delete_event / truncate methods — the
# append-only contract is part of the API (D195).


__all__ = [
    "DuplicateEventIdError",
    "write_event",
    "list_events_for_session",
    "count_events",
    "utc_now",
]


# Compatibility shim: pg_insert re-exported for tests that want to hit
# ON CONFLICT semantics explicitly. Not used by production code.
_pg_insert = pg_insert
