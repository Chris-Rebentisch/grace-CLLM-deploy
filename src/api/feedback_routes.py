"""Retrieval feedback API surface (Chunk 35a, D266).

Single mutating route:

* ``POST /api/feedback/retrieval`` — accepts a thumbs-up / thumbs-down
  vote scoped to a ``query_event_id`` plus optional freetext (max 2048
  chars). Persists one ``retrieval_feedback`` row per request. Append
  only — no idempotency / dedup; the signal pipeline aggregates votes
  downstream (Chunk 35b).

Inherits the Chunk 31 default-deny admission tree from ``AuthMiddleware``;
not added to ``READONLY_ROUTES``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Column,
    DateTime,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from src.shared.database import Base, get_db

logger = structlog.get_logger()


router = APIRouter(prefix="/api/feedback", tags=["feedback"])


# --- ORM model ------------------------------------------------------------


class RetrievalFeedbackRow(Base):
    """SQLAlchemy ORM model for the ``retrieval_feedback`` table.

    See ``alembic/versions/c35a_retrieval_feedback.py`` for the canonical
    column definitions and CHECK constraints.
    """

    __tablename__ = "retrieval_feedback"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    query_event_id = Column(Text, nullable=False, index=True)
    vote = Column(Text, nullable=False)
    freetext = Column(Text, nullable=True)
    submitted_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- Schema ---------------------------------------------------------------


class FeedbackRequest(BaseModel):
    """Request body for ``POST /api/feedback/retrieval`` (D266)."""

    model_config = ConfigDict(extra="forbid")

    query_event_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Opaque correlation id for the retrieval response that produced "
            "this feedback. Treated as a string (not a UUID) so 35a does not "
            "constrain 35b's query-event identifier shape."
        ),
    )
    vote: str = Field(
        pattern=r"^(up|down)$",
        description="Thumbs-up or thumbs-down. Only 'up' / 'down' allowed.",
    )
    freetext: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "Optional reviewer-supplied freetext (max 2048 chars). Mirrors "
            "the DB CHECK constraint."
        ),
    )


class FeedbackResponse(BaseModel):
    """Response for ``POST /api/feedback/retrieval`` (D266)."""

    feedback_id: UUID
    query_event_id: str
    vote: str
    submitted_at: datetime


# --- Route ----------------------------------------------------------------


@router.post(
    "/retrieval",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_retrieval_feedback(
    request: FeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    """Persist a single retrieval-feedback row and return its identifiers."""
    row = RetrievalFeedbackRow(
        query_event_id=request.query_event_id,
        vote=request.vote,
        freetext=request.freetext,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    logger.info(
        "feedback.retrieval.submitted",
        feedback_id=str(row.id),
        query_event_id=row.query_event_id,
        vote=row.vote,
        has_freetext=row.freetext is not None,
    )

    return FeedbackResponse(
        feedback_id=row.id,
        query_event_id=row.query_event_id,
        vote=row.vote,
        submitted_at=row.submitted_at,
    )
