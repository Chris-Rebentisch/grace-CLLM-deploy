"""ORM model + CRUD for the elicitation_sessions table (D223).

Mirrors the hybrid columnar+JSONB pattern from CQTestRunRow at
``src/ontology/cq_test_runner.py:33``. Append-only constraint via
PostgreSQL trigger; lifecycle fields (current_phase, paused_at,
closed_at, session_plan_jsonb) are updatable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Column, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.shared.database import Base

logger = structlog.get_logger()


class ElicitationSessionRow(Base):
    """SQLAlchemy ORM model for the elicitation_sessions table."""

    __tablename__ = "elicitation_sessions"

    session_id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_type = Column(Text, nullable=False)
    current_phase = Column(Text, nullable=False)
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    paused_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    session_plan_jsonb = Column(JSONB, nullable=False)
    metadata_extra = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_elicitation_sessions_actor_type", "actor_type"),
        Index("ix_elicitation_sessions_current_phase", "current_phase"),
        Index("ix_elicitation_sessions_started_at", "started_at"),
    )


def create_session(
    db: Session,
    *,
    session_id: UUID | None = None,
    actor_type: str = "human",
    current_phase: str = "open",
    session_plan: dict | None = None,
    metadata_extra: dict | None = None,
) -> ElicitationSessionRow:
    """Insert a new elicitation session row."""
    row = ElicitationSessionRow(
        session_id=session_id or uuid4(),
        actor_type=actor_type,
        current_phase=current_phase,
        session_plan_jsonb=session_plan or {},
        metadata_extra=metadata_extra or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "elicitation_session.created",
        session_id=str(row.session_id),
        actor_type=row.actor_type,
        current_phase=row.current_phase,
    )
    return row


def get_session(db: Session, session_id: UUID) -> ElicitationSessionRow | None:
    """Fetch a session by its primary key."""
    return (
        db.query(ElicitationSessionRow)
        .filter(ElicitationSessionRow.session_id == session_id)
        .first()
    )


def update_phase(
    db: Session, session_id: UUID, new_phase: str
) -> ElicitationSessionRow | None:
    """Update the current_phase of an existing session."""
    row = get_session(db, session_id)
    if row is None:
        return None
    row.current_phase = new_phase
    db.commit()
    db.refresh(row)
    logger.info(
        "elicitation_session.phase_updated",
        session_id=str(session_id),
        new_phase=new_phase,
    )
    return row


def close_session(db: Session, session_id: UUID) -> ElicitationSessionRow | None:
    """Mark a session as closed."""
    row = get_session(db, session_id)
    if row is None:
        return None
    row.current_phase = "close"
    row.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    logger.info(
        "elicitation_session.closed",
        session_id=str(session_id),
    )
    return row
