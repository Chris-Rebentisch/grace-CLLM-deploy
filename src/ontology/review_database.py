"""SQLAlchemy ORM tables and CRUD operations for Guided Review."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.ontology.review_models import (
    ChangeOfStatusEntityType,
    ChangeOfStatusEvent,
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementStatus,
    ReviewElementType,
    ReviewSession,
    ReviewSessionStatus,
)
from src.shared.database import Base

log = structlog.get_logger()


# --- ORM Row Classes ---


class ReviewSessionRow(Base):
    """SQLAlchemy ORM model for the review_sessions table."""

    __tablename__ = "review_sessions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="in_progress")
    reviewer = Column(Text, nullable=False)
    seed_schema_merge_run_id = Column(Text, nullable=False)
    seed_schema_snapshot = Column(JSONB, nullable=False)
    total_entity_types = Column(Integer, nullable=False, default=0)
    total_relationships = Column(Integer, nullable=False, default=0)
    reviewed_entity_types = Column(Integer, nullable=False, default=0)
    reviewed_relationships = Column(Integer, nullable=False, default=0)
    resulting_version_id = Column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    metadata_extra = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_review_sessions_status", "status"),
        Index("ix_review_sessions_created_at", "created_at"),
    )


class ReviewDecisionRow(Base):
    """SQLAlchemy ORM model for the review_decisions table."""

    __tablename__ = "review_decisions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(PG_UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    element_type = Column(String(20), nullable=False)
    element_name = Column(Text, nullable=False)
    decision = Column(String(20), nullable=False)
    original_data = Column(JSONB, nullable=False)
    modified_data = Column(JSONB, nullable=True)
    split_into = Column(JSONB, nullable=True)
    merged_with = Column(Text, nullable=True)
    reviewer = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)
    cq_impact = Column(JSONB, nullable=True)
    metadata_extra = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_review_decisions_session_id", "session_id"),
        Index("ix_review_decisions_element_type", "element_type"),
        Index("ix_review_decisions_element_name", "element_name"),
        Index("ix_review_decisions_decision", "decision"),
    )


class ChangeOfStatusEventRow(Base):
    """SQLAlchemy ORM model for the change_of_status_events table."""

    __tablename__ = "change_of_status_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(PG_UUID(as_uuid=True), nullable=False)
    from_status = Column(Text, nullable=False)
    to_status = Column(Text, nullable=False)
    agent = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    metadata_extra = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_change_of_status_entity_id", "entity_id"),
        Index("ix_change_of_status_entity_type", "entity_type"),
        Index("ix_change_of_status_created_at", "created_at"),
    )


# --- Row-to-Model / Model-to-Row Converters ---


def _session_row_to_model(row: ReviewSessionRow) -> ReviewSession:
    """Convert a SQLAlchemy ReviewSessionRow to a Pydantic ReviewSession."""
    return ReviewSession(
        id=row.id,
        created_at=row.created_at,
        completed_at=row.completed_at,
        status=ReviewSessionStatus(row.status),
        reviewer=row.reviewer,
        seed_schema_merge_run_id=row.seed_schema_merge_run_id,
        seed_schema_snapshot=row.seed_schema_snapshot,
        total_entity_types=row.total_entity_types,
        total_relationships=row.total_relationships,
        reviewed_entity_types=row.reviewed_entity_types,
        reviewed_relationships=row.reviewed_relationships,
        resulting_version_id=row.resulting_version_id,
        metadata_extra=row.metadata_extra or {},
    )


def _session_model_to_row(session: ReviewSession) -> ReviewSessionRow:
    """Convert a Pydantic ReviewSession to a SQLAlchemy ReviewSessionRow."""
    return ReviewSessionRow(
        id=session.id,
        created_at=session.created_at,
        completed_at=session.completed_at,
        status=session.status.value,
        reviewer=session.reviewer,
        seed_schema_merge_run_id=session.seed_schema_merge_run_id,
        seed_schema_snapshot=session.seed_schema_snapshot,
        total_entity_types=session.total_entity_types,
        total_relationships=session.total_relationships,
        reviewed_entity_types=session.reviewed_entity_types,
        reviewed_relationships=session.reviewed_relationships,
        resulting_version_id=session.resulting_version_id,
        metadata_extra=session.metadata_extra,
    )


def _decision_row_to_model(row: ReviewDecisionRow) -> ReviewDecision:
    """Convert a SQLAlchemy ReviewDecisionRow to a Pydantic ReviewDecision."""
    return ReviewDecision(
        id=row.id,
        session_id=row.session_id,
        created_at=row.created_at,
        element_type=ReviewElementType(row.element_type),
        element_name=row.element_name,
        decision=ReviewDecisionType(row.decision),
        original_data=row.original_data,
        modified_data=row.modified_data,
        split_into=row.split_into,
        merged_with=row.merged_with,
        reviewer=row.reviewer,
        notes=row.notes,
        cq_impact=row.cq_impact,
        metadata_extra=row.metadata_extra or {},
    )


def _decision_model_to_row(decision: ReviewDecision) -> ReviewDecisionRow:
    """Convert a Pydantic ReviewDecision to a SQLAlchemy ReviewDecisionRow."""
    return ReviewDecisionRow(
        id=decision.id,
        session_id=decision.session_id,
        created_at=decision.created_at,
        element_type=decision.element_type.value,
        element_name=decision.element_name,
        decision=decision.decision.value,
        original_data=decision.original_data,
        modified_data=decision.modified_data,
        split_into=decision.split_into,
        merged_with=decision.merged_with,
        reviewer=decision.reviewer,
        notes=decision.notes,
        cq_impact=decision.cq_impact,
        metadata_extra=decision.metadata_extra,
    )


def _status_event_row_to_model(row: ChangeOfStatusEventRow) -> ChangeOfStatusEvent:
    """Convert a SQLAlchemy ChangeOfStatusEventRow to a Pydantic ChangeOfStatusEvent."""
    return ChangeOfStatusEvent(
        id=row.id,
        created_at=row.created_at,
        entity_type=ChangeOfStatusEntityType(row.entity_type),
        entity_id=row.entity_id,
        from_status=row.from_status,
        to_status=row.to_status,
        agent=row.agent,
        reason=row.reason,
        metadata_extra=row.metadata_extra or {},
    )


def _status_event_model_to_row(event: ChangeOfStatusEvent) -> ChangeOfStatusEventRow:
    """Convert a Pydantic ChangeOfStatusEvent to a SQLAlchemy ChangeOfStatusEventRow."""
    return ChangeOfStatusEventRow(
        id=event.id,
        created_at=event.created_at,
        entity_type=event.entity_type.value,
        entity_id=event.entity_id,
        from_status=event.from_status,
        to_status=event.to_status,
        agent=event.agent,
        reason=event.reason,
        metadata_extra=event.metadata_extra,
    )


# --- CRUD Functions: ReviewSession ---


def create_review_session(db: Session, session: ReviewSession) -> ReviewSession:
    """Insert a new review session. Also creates a ChangeOfStatus event (None -> in_progress)."""
    row = _session_model_to_row(session)
    db.add(row)
    db.flush()

    # Record the initial status transition
    status_event = ChangeOfStatusEventRow(
        entity_type=ChangeOfStatusEntityType.REVIEW_SESSION.value,
        entity_id=row.id,
        from_status="none",
        to_status=ReviewSessionStatus.IN_PROGRESS.value,
        agent=session.reviewer,
        reason="Review session started",
    )
    db.add(status_event)
    db.commit()
    db.refresh(row)
    log.info("review_session_created", session_id=str(row.id), reviewer=session.reviewer)
    return _session_row_to_model(row)


def get_review_session_by_id(db: Session, session_id: UUID) -> ReviewSession | None:
    """Retrieve a review session by UUID."""
    row = db.query(ReviewSessionRow).filter(ReviewSessionRow.id == session_id).first()
    return _session_row_to_model(row) if row else None


def list_review_sessions(
    db: Session,
    status: ReviewSessionStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ReviewSession]:
    """List review sessions with optional status filter. Ordered by created_at descending."""
    query = db.query(ReviewSessionRow)
    if status is not None:
        query = query.filter(ReviewSessionRow.status == status.value)
    rows = query.order_by(ReviewSessionRow.created_at.desc()).offset(offset).limit(limit).all()
    return [_session_row_to_model(row) for row in rows]


def update_review_session_status(
    db: Session,
    session_id: UUID,
    new_status: ReviewSessionStatus,
    agent: str,
    reason: str | None = None,
    resulting_version_id: UUID | None = None,
) -> ReviewSession | None:
    """Update session status. Creates a ChangeOfStatus event for the transition.
    Sets completed_at when status changes to COMPLETED or ABANDONED.
    Sets resulting_version_id when provided (on completion)."""
    row = db.query(ReviewSessionRow).filter(ReviewSessionRow.id == session_id).first()
    if row is None:
        return None

    old_status = row.status
    row.status = new_status.value

    if new_status in (ReviewSessionStatus.COMPLETED, ReviewSessionStatus.ABANDONED):
        row.completed_at = datetime.now(UTC)

    if resulting_version_id is not None:
        row.resulting_version_id = resulting_version_id

    db.flush()

    # Record the status transition
    status_event = ChangeOfStatusEventRow(
        entity_type=ChangeOfStatusEntityType.REVIEW_SESSION.value,
        entity_id=session_id,
        from_status=old_status,
        to_status=new_status.value,
        agent=agent,
        reason=reason,
    )
    db.add(status_event)
    db.commit()
    db.refresh(row)
    log.info(
        "review_session_status_updated",
        session_id=str(session_id),
        old_status=old_status,
        new_status=new_status.value,
    )
    return _session_row_to_model(row)


def increment_reviewed_count(
    db: Session,
    session_id: UUID,
    element_type: ReviewElementType,
) -> ReviewSession | None:
    """Increment reviewed_entity_types or reviewed_relationships counter by 1."""
    row = db.query(ReviewSessionRow).filter(ReviewSessionRow.id == session_id).first()
    if row is None:
        return None

    if element_type == ReviewElementType.ENTITY_TYPE:
        row.reviewed_entity_types = (row.reviewed_entity_types or 0) + 1
    else:
        row.reviewed_relationships = (row.reviewed_relationships or 0) + 1

    db.commit()
    db.refresh(row)
    return _session_row_to_model(row)


def get_review_progress(db: Session, session_id: UUID) -> dict | None:
    """Return progress summary for a review session.

    Returns None if session not found.
    """
    row = db.query(ReviewSessionRow).filter(ReviewSessionRow.id == session_id).first()
    if row is None:
        return None

    total = (row.total_entity_types or 0) + (row.total_relationships or 0)
    reviewed = (row.reviewed_entity_types or 0) + (row.reviewed_relationships or 0)
    overall_percent = (reviewed / total * 100.0) if total > 0 else 0.0

    return {
        "entity_types": {
            "total": row.total_entity_types or 0,
            "reviewed": row.reviewed_entity_types or 0,
            "remaining": (row.total_entity_types or 0) - (row.reviewed_entity_types or 0),
        },
        "relationships": {
            "total": row.total_relationships or 0,
            "reviewed": row.reviewed_relationships or 0,
            "remaining": (row.total_relationships or 0) - (row.reviewed_relationships or 0),
        },
        "overall_percent": overall_percent,
        "status": row.status,
    }


# --- CRUD Functions: ReviewDecision ---


def create_review_decision(db: Session, decision: ReviewDecision) -> ReviewDecision:
    """Insert a new review decision. Also creates a ChangeOfStatus event for the element
    (pending -> decided)."""
    row = _decision_model_to_row(decision)
    db.add(row)
    db.flush()

    # Record the element status transition
    status_event = ChangeOfStatusEventRow(
        entity_type=ChangeOfStatusEntityType.REVIEW_ELEMENT.value,
        entity_id=row.id,
        from_status=ReviewElementStatus.PENDING.value,
        to_status=ReviewElementStatus.DECIDED.value,
        agent=decision.reviewer,
        reason=f"Decision: {decision.decision.value} on {decision.element_name}",
    )
    db.add(status_event)
    db.commit()
    db.refresh(row)
    log.info(
        "review_decision_created",
        decision_id=str(row.id),
        element_name=decision.element_name,
        decision=decision.decision.value,
    )
    return _decision_row_to_model(row)


def get_review_decision_by_id(db: Session, decision_id: UUID) -> ReviewDecision | None:
    """Retrieve a decision by UUID."""
    row = db.query(ReviewDecisionRow).filter(ReviewDecisionRow.id == decision_id).first()
    return _decision_row_to_model(row) if row else None


def list_decisions_for_session(
    db: Session,
    session_id: UUID,
    element_type: ReviewElementType | None = None,
    decision: ReviewDecisionType | None = None,
) -> list[ReviewDecision]:
    """List all decisions in a session with optional filters. Ordered by created_at."""
    query = db.query(ReviewDecisionRow).filter(ReviewDecisionRow.session_id == session_id)
    if element_type is not None:
        query = query.filter(ReviewDecisionRow.element_type == element_type.value)
    if decision is not None:
        query = query.filter(ReviewDecisionRow.decision == decision.value)
    rows = query.order_by(ReviewDecisionRow.created_at.asc()).all()
    return [_decision_row_to_model(row) for row in rows]


def get_decision_for_element(
    db: Session,
    session_id: UUID,
    element_name: str,
) -> ReviewDecision | None:
    """Get the decision for a specific element in a session.

    Returns the most recent decision if the element was reviewed multiple times.
    """
    row = (
        db.query(ReviewDecisionRow)
        .filter(
            ReviewDecisionRow.session_id == session_id,
            ReviewDecisionRow.element_name == element_name,
        )
        .order_by(ReviewDecisionRow.created_at.desc())
        .first()
    )
    return _decision_row_to_model(row) if row else None


def get_decision_summary(db: Session, session_id: UUID) -> dict:
    """Return counts by decision type and element type for a session."""
    decisions = (
        db.query(ReviewDecisionRow)
        .filter(ReviewDecisionRow.session_id == session_id)
        .all()
    )

    by_decision: dict[str, int] = {}
    by_element_type: dict[str, int] = {}

    for row in decisions:
        by_decision[row.decision] = by_decision.get(row.decision, 0) + 1
        by_element_type[row.element_type] = by_element_type.get(row.element_type, 0) + 1

    return {
        "by_decision": by_decision,
        "by_element_type": by_element_type,
        "total": len(decisions),
    }


# --- CRUD Functions: ChangeOfStatusEvent ---


def create_change_of_status(db: Session, event: ChangeOfStatusEvent) -> ChangeOfStatusEvent:
    """Insert a new change-of-status event."""
    row = _status_event_model_to_row(event)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(
        "change_of_status_created",
        event_id=str(row.id),
        entity_type=event.entity_type.value,
        from_status=event.from_status,
        to_status=event.to_status,
    )
    return _status_event_row_to_model(row)


def list_status_changes_for_entity(
    db: Session,
    entity_id: UUID,
) -> list[ChangeOfStatusEvent]:
    """List all status transitions for a given entity. Ordered by created_at."""
    rows = (
        db.query(ChangeOfStatusEventRow)
        .filter(ChangeOfStatusEventRow.entity_id == entity_id)
        .order_by(ChangeOfStatusEventRow.created_at.asc())
        .all()
    )
    return [_status_event_row_to_model(row) for row in rows]


def list_status_changes_by_type(
    db: Session,
    entity_type: ChangeOfStatusEntityType,
    limit: int = 100,
) -> list[ChangeOfStatusEvent]:
    """List status changes filtered by entity type. Ordered by created_at descending."""
    rows = (
        db.query(ChangeOfStatusEventRow)
        .filter(ChangeOfStatusEventRow.entity_type == entity_type.value)
        .order_by(ChangeOfStatusEventRow.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_status_event_row_to_model(row) for row in rows]


def get_status_duration(db: Session, entity_id: UUID, status: str) -> float | None:
    """Calculate how long (in seconds) an entity spent in a given status.

    Uses the transition timestamps: time between entering the status and leaving it.
    Returns None if entity never had this status or is still in it.
    """
    events = (
        db.query(ChangeOfStatusEventRow)
        .filter(ChangeOfStatusEventRow.entity_id == entity_id)
        .order_by(ChangeOfStatusEventRow.created_at.asc())
        .all()
    )

    entered_at = None
    for event in events:
        if event.to_status == status and entered_at is None:
            entered_at = event.created_at
        elif event.from_status == status and entered_at is not None:
            duration = (event.created_at - entered_at).total_seconds()
            return duration

    # Never entered this status, or still in it
    return None
