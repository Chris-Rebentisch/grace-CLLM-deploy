"""FastAPI review endpoints for Guided Review workflow."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.ontology.review_database import (
    get_review_session_by_id,
    list_review_sessions,
    get_review_progress,
    get_decision_summary,
)
from src.ontology.review_models import (
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementType,
    ReviewSessionStatus,
)
from src.ontology.review_ops import (
    abandon_review_session,
    complete_review_session,
    compute_cq_impact_for_decision,
    compute_cq_impact_preview,
    get_element_review_status,
    start_review_session,
)
from src.ontology.review_database import (
    create_review_decision,
    increment_reviewed_count,
    list_decisions_for_session,
)
from src.elicitation.bridge import enqueue_event
from src.shared.database import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/ontology/review", tags=["review"])


def _emit_review_event(event_type: str, payload: dict, session_id: UUID, reviewer: str) -> None:
    """F-014 / ISS-0012: server-side audit emission for REST-driven review actions.

    Driving ``/api/ontology/review/*`` directly (curl, scripts, third-party UI)
    previously left ``elicitation_events`` empty — event capture was entirely
    client-side. Best-effort: an audit-event failure must never break the route.
    Server-emitted rows carry ``actor_type="system"`` (set by the bridge) so
    they stay distinguishable from any client-emitted duplicate (append-only
    double emission is acceptable); the request's ``reviewer`` rides along as
    envelope ``agent_id``.
    """
    try:
        enqueue_event(
            event_type=event_type,
            payload=payload,
            session_id_override=session_id,
            agent_id=reviewer,
            delegation_source="user_direct",
        )
    except Exception as exc:  # noqa: BLE001 — log-and-continue by design
        log.warning(
            "review.audit_event_failed",
            event_type=event_type,
            session_id=str(session_id),
            error=str(exc),
        )


# --- Request Models ---


class StartReviewRequest(BaseModel):
    """Request body for starting a review session."""

    merge_run_id: str
    reviewer: str
    seed_schema_data: dict


class DecideRequest(BaseModel):
    """Request body for recording a review decision."""

    element_type: ReviewElementType
    element_name: str
    decision: ReviewDecisionType
    modified_data: dict | None = None
    split_into: list[dict] | None = None
    merged_with: str | None = None
    reviewer: str
    notes: str | None = None


class CompleteRequest(BaseModel):
    """Request body for completing a review session."""

    reviewer: str
    force: bool = False


class AbandonRequest(BaseModel):
    """Request body for abandoning a review session."""

    agent: str
    reason: str | None = None


class AssistTurnBody(BaseModel):
    """One prior message in the review-assist drawer conversation."""

    role: str
    content: str


class AssistRequest(BaseModel):
    """Request body for the conversational review assistant (D522 session)."""

    element_type: ReviewElementType
    element_name: str
    message: str
    history: list[AssistTurnBody] = []


# --- Endpoints ---


@router.post("/start")
def start_review(body: StartReviewRequest, db: Session = Depends(get_db)):
    """Start a new review session from a SeedSchema."""
    session = start_review_session(
        db=db,
        merge_run_id=body.merge_run_id,
        reviewer=body.reviewer,
        seed_schema_data=body.seed_schema_data,
    )
    return session.model_dump(mode="json")


@router.get("/sessions")
def list_sessions(
    status: str | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db: Session = Depends(get_db),
):
    """List review sessions with optional status filter."""
    status_enum = ReviewSessionStatus(status) if status else None
    sessions = list_review_sessions(db, status=status_enum, limit=limit, offset=offset)
    return [s.model_dump(mode="json") for s in sessions]


@router.get("/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    """Get review session by ID."""
    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    return session.model_dump(mode="json")


@router.get("/{session_id}/elements")
def get_elements(session_id: UUID, db: Session = Depends(get_db)):
    """Get the review status of every element in the session."""
    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    return get_element_review_status(db, session_id)


@router.post("/{session_id}/decide")
def decide(session_id: UUID, body: DecideRequest, db: Session = Depends(get_db)):
    """Record a review decision on a schema element."""
    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    if session.status != ReviewSessionStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail=f"Session is {session.status.value}, not in_progress",
        )

    # Find original element from seed schema
    original_data = _find_element_in_snapshot(
        session.seed_schema_snapshot, body.element_type, body.element_name
    )
    if original_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Element '{body.element_name}' not found in seed schema",
        )

    # Get existing decisions for CQ impact
    existing_decisions = list_decisions_for_session(db, session_id)

    # Create decision
    decision = ReviewDecision(
        session_id=session_id,
        element_type=body.element_type,
        element_name=body.element_name,
        decision=body.decision,
        original_data=original_data,
        modified_data=body.modified_data,
        split_into=body.split_into,
        merged_with=body.merged_with,
        reviewer=body.reviewer,
        notes=body.notes,
    )

    # Compute CQ impact
    cq_impact = compute_cq_impact_for_decision(
        session.seed_schema_snapshot, existing_decisions, decision
    )
    decision.cq_impact = cq_impact

    # Save
    created = create_review_decision(db, decision)
    increment_reviewed_count(db, session_id, body.element_type)

    # F-014 / ISS-0012: server-side audit trail for the decide gate.
    # ``mcp_review_decided`` is the existing EventType for "a reviewer decided
    # one schema element" (no new enum members invented).
    _emit_review_event(
        event_type="mcp_review_decided",
        payload={
            "session_id": str(session_id),
            "element_name": body.element_name,
            "decision": body.decision.value,
            "rationale": body.notes,
            "agent_id": body.reviewer,
        },
        session_id=session_id,
        reviewer=body.reviewer,
    )

    return {
        "decision": created.model_dump(mode="json"),
        "cq_impact": cq_impact,
    }


@router.post("/{session_id}/assist")
async def assist(session_id: UUID, body: AssistRequest, db: Session = Depends(get_db)):
    """Conversational, plain-language help for one proposed element (D522 session).

    Read-only with respect to the review session — the assistant only explains and
    proposes an action; the reviewer confirms separately via ``/decide``.
    """
    from src.ontology.review_assist import (
        AssistTurn,
        run_review_assist,
    )
    from src.ontology.review_ops import _build_cq_text_map, _resolve_questions

    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Review session not found")

    raw = _find_element_in_snapshot(
        session.seed_schema_snapshot, body.element_type, body.element_name
    )
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Element '{body.element_name}' not found in seed schema",
        )

    # Resolve the type's CQ IDs to real business questions for grounding.
    cq_map = _build_cq_text_map(db)
    element = {
        "name": raw.get("name", body.element_name),
        "display_label": raw.get("display_label") or "",
        "description": raw.get("description") or "",
        "plain_description": raw.get("plain_description") or "",
        "example_snippet": raw.get("example_snippet"),
        "evidence_document_count": raw.get("evidence_document_count", 0),
        "answerable_questions": _resolve_questions(raw.get("answerable_cqs", []) or [], cq_map),
    }

    # Other type names on the list (for merge suggestions).
    snapshot_key = (
        "entity_types"
        if body.element_type == ReviewElementType.ENTITY_TYPE
        else "relationships"
    )
    other_type_names = [
        e.get("name")
        for e in session.seed_schema_snapshot.get(snapshot_key, [])
        if e.get("name") and e.get("name") != element["name"]
    ]

    history = [AssistTurn(role=t.role, content=t.content) for t in body.history]
    result = await run_review_assist(
        element=element,
        other_type_names=other_type_names,
        history=history,
        message=body.message,
    )
    return result.model_dump(mode="json")


@router.get("/{session_id}/cq-impact/{element_name}")
def cq_impact_preview(
    session_id: UUID,
    element_name: str,
    decision: str = Query(...),
    db: Session = Depends(get_db),
):
    """Preview CQ impact of a hypothetical decision."""
    session = get_review_session_by_id(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Review session not found")

    existing_decisions = list_decisions_for_session(db, session_id)
    decision_type = ReviewDecisionType(decision)

    return compute_cq_impact_preview(
        session.seed_schema_snapshot,
        existing_decisions,
        element_name,
        decision_type,
    )


@router.get("/{session_id}/progress")
def progress(session_id: UUID, db: Session = Depends(get_db)):
    """Get review progress for a session."""
    result = get_review_progress(db, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    return result


@router.post("/{session_id}/complete")
def complete(session_id: UUID, body: CompleteRequest, db: Session = Depends(get_db)):
    """Complete a review session and ratify the schema."""
    try:
        result = complete_review_session(
            db=db,
            session_id=session_id,
            reviewer=body.reviewer,
            force=body.force,
        )
        # F-014 / ISS-0012: server-side audit trail for the complete gate.
        # ``mcp_session_closed`` is the existing "review session finished"
        # EventType (no new enum members invented).
        _emit_review_event(
            event_type="mcp_session_closed",
            payload={
                "session_id": str(session_id),
                "agent_id": body.reviewer,
            },
            session_id=session_id,
            reviewer=body.reviewer,
        )
        return result
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)


@router.post("/{session_id}/abandon")
def abandon(session_id: UUID, body: AbandonRequest, db: Session = Depends(get_db)):
    """Abandon a review session."""
    result = abandon_review_session(
        db=db,
        session_id=session_id,
        agent=body.agent,
        reason=body.reason,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    return result.model_dump(mode="json")


def _find_element_in_snapshot(
    snapshot: dict, element_type: ReviewElementType, element_name: str
) -> dict | None:
    """Find an element's data in the seed schema snapshot."""
    if element_type == ReviewElementType.ENTITY_TYPE:
        for et in snapshot.get("entity_types", []):
            if et.get("name") == element_name:
                return et
    else:
        for rel in snapshot.get("relationships", []):
            if rel.get("name") == element_name:
                return rel
    return None
