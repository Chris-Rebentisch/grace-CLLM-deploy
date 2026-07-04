"""In-process enqueue for server-originated elicitation events (Chunk 41/42).

Routes and CLIs that cannot inject :class:`fastapi.Depends` use this helper
to validate payloads and append rows via :func:`write_event` with a dedicated
DB session. Failures are logged at the call site when best-effort semantics
apply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from src.elicitation.event_writer import write_event
from src.elicitation.models import (
    ElicitationEventEnvelope,
    EventType,
    validate_payload_for_event_type,
)
from src.shared.database import get_session_factory


def _correlation_session_id(event_type: str, payload: dict[str, Any]) -> UUID:
    """Derive a stable session anchor for server pipeline telemetry."""
    if event_type == "permission_matrix_auto_assigned":
        person = str(payload.get("person_grace_id", ""))
        return uuid5(NAMESPACE_URL, f"grace:permissions:drift:{person}")
    for key in ("run_id", "matrix_id"):
        if key in payload:
            try:
                return UUID(str(payload[key]))
            except (ValueError, TypeError):
                break
    return UUID(int=0)


def enqueue_event(
    *,
    event_type: str,
    payload: dict[str, Any],
    db: Any | None = None,
    session_id_override: UUID | None = None,
    agent_id: str | None = None,
    agent_display_name: str | None = None,
    delegation_source: str | None = None,
) -> None:
    """Validate ``payload`` for ``event_type`` and append one ``elicitation_events`` row.

    When ``db`` is provided, reuses that SQLAlchemy session (single-transaction
    semantics — caller owns commit/rollback). When ``session_id_override`` is
    provided, uses it as the envelope's ``session_id`` instead of the derived
    correlation session ID.

    Both parameters are backwards-compatible optional additions (D446, Chunk 65).

    F-014 / ISS-0012: ``agent_id`` / ``agent_display_name`` / ``delegation_source``
    are backwards-compatible optional additions so route handlers can carry the
    request's ``reviewer`` field into the D364 envelope agent-identity columns.
    Server-emitted rows stay distinguishable from client-emitted ones via the
    fixed ``actor_type="system"`` below (clients emit ``human``/``agent``).
    """
    validated = validate_payload_for_event_type(event_type, payload)
    payload_dict = validated.model_dump(mode="json")
    sid = session_id_override if session_id_override is not None else _correlation_session_id(event_type, payload_dict)
    envelope = ElicitationEventEnvelope(
        event_id=uuid4(),
        event_type=cast(EventType, event_type),
        session_id=sid,
        actor_type="system",
        phase_name="none",
        emitted_at=datetime.now(timezone.utc),
        schema_version=1,
        grace_version="0.1.0",
        payload=payload_dict,
        payload_schema_version=1,
        agent_id=agent_id,
        agent_display_name=agent_display_name,
        delegation_source=cast(Any, delegation_source),
    )
    if db is not None:
        # Transactional mode: INSERT without committing — caller owns the transaction.
        # write_event() internally calls session.commit(), which would break the
        # single-transaction semantics required by D446.
        from src.elicitation.event_writer import _envelope_to_row
        from src.elicitation.schema import elicitation_events
        from sqlalchemy import insert
        row = _envelope_to_row(envelope)
        db.execute(insert(elicitation_events).values(**row))
    else:
        session_factory = get_session_factory()
        own_db = session_factory()
        try:
            write_event(own_db, envelope)
        finally:
            own_db.close()


__all__ = ["enqueue_event"]
