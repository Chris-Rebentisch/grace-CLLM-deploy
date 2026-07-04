"""POST /api/elicitation/events — telemetry ingest (Chunk 27, D195, D202).

Validates the envelope + per-event-type payload, writes append-only to
`elicitation_events`. Returns the accepted event_id and receipt time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.analytics.metrics import change_directive_detail_viewed_total
from src.elicitation.event_writer import (
    DuplicateEventIdError,
    write_event,
)
from src.elicitation.models import (
    ElicitationEventAck,
    ElicitationEventEnvelope,
    validate_payload_for_event_type,
)
from src.shared.database import get_db

router = APIRouter(prefix="/api/elicitation", tags=["elicitation"])
logger = structlog.get_logger()


@router.post(
    "/events",
    response_model=ElicitationEventAck,
    status_code=status.HTTP_201_CREATED,
)
def post_event(
    envelope: ElicitationEventEnvelope,
    db: Session = Depends(get_db),
) -> ElicitationEventAck:
    # Cross-field validation: payload shape must match event_type.
    try:
        validate_payload_for_event_type(envelope.event_type, envelope.payload)
    except ValidationError as err:
        logger.warning(
            "elicitation.validation_failed",
            event_type=envelope.event_type,
            errors=err.errors(),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_type": "telemetry_validation_error",
                "errors": err.errors(),
            },
        )

    try:
        write_event(db, envelope)
        if envelope.event_type == "change_directive_detail_viewed":
            change_directive_detail_viewed_total.add(1)
    except DuplicateEventIdError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "duplicate_event_id",
                "event_id": str(envelope.event_id),
                "message": str(err),
            },
        )

    return ElicitationEventAck(
        event_id=envelope.event_id,
        accepted_at=datetime.now(timezone.utc),
    )


def _lookup_event_id_for_test(session: Session, event_id: UUID) -> bool:
    """Helper used only by tests to check append-only guarantees."""
    from sqlalchemy import select

    from src.elicitation.schema import elicitation_events

    stmt = select(elicitation_events).where(
        elicitation_events.c.event_id == event_id
    )
    return bool(session.execute(stmt).first())
