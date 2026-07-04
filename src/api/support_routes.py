"""Support session API routes (Chunk 45, D372/D373/D374).

Five admin routes (``/api/admin/support-sessions``) + one public route
(``GET /api/support/status``).

Admin GET routes require route-local admin-key enforcement because
middleware step 2 admits all GET/HEAD/OPTIONS unconditionally.
"""

from __future__ import annotations

import os
import secrets
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import JSONResponse

from src.shared.database import get_session_factory
from src.support.models import (
    SupportSessionCreate,
    SupportSessionIssuanceResponse,
    SupportSessionResponse,
    SupportStatusResponse,
    TranscriptResponse,
)
from src.support.session_manager import (
    create_session,
    get_active_status,
    get_session,
    list_sessions,
    revoke_session,
)

logger = structlog.get_logger()

router = APIRouter(tags=["support"])


def _require_admin_key(request: Request) -> None:
    """Route-local admin-key enforcement for admin GET routes.

    Middleware step 2 admits all GET/HEAD/OPTIONS unconditionally, so
    admin-key checking for GET routes must be done at the route level.
    """
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        # No key configured — check loopback.
        client_host = request.client.host if request.client else None
        if client_host in {"127.0.0.1", "::1", "testclient"}:
            return
        raise HTTPException(status_code=401, detail="admin key required")

    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted or not secrets.compare_digest(admin_key, submitted):
        raise HTTPException(status_code=401, detail="admin key required")


def _get_db():
    factory = get_session_factory()
    return factory()


def _emit_elicitation_event(
    db, event_type: str, payload: dict, session_id: str = "00000000-0000-0000-0000-000000000000"
) -> None:
    """Best-effort elicitation event emission. Never fails the route."""
    try:
        from datetime import UTC, datetime
        from uuid import uuid4
        from src.elicitation.models import ElicitationEventEnvelope, validate_payload_for_event_type
        from sqlalchemy import text

        validate_payload_for_event_type(event_type, payload)
        event_id = uuid4()
        now = datetime.now(UTC)
        db.execute(
            text(
                "INSERT INTO elicitation_events "
                "(event_id, event_type, session_id, actor_type, phase_name, "
                " emitted_at, schema_version, grace_version, payload, payload_schema_version) "
                "VALUES (:eid, :etype, :sid, :actor, :phase, :emitted, :sv, :gv, "
                "        CAST(:payload AS jsonb), :psv)"
            ),
            {
                "eid": event_id,
                "etype": event_type,
                "sid": session_id,
                "actor": "system",
                "phase": "none",
                "emitted": now,
                "sv": 1,
                "gv": "0.1.0",
                "payload": __import__("json").dumps(payload),
                "psv": 1,
            },
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("support.elicitation_event.failed", error=str(exc))
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


# --- Admin routes ---


@router.post(
    "/api/admin/support-sessions",
    response_model=SupportSessionIssuanceResponse,
    status_code=201,
)
async def create_support_session(
    body: SupportSessionCreate,
    request: Request,
) -> SupportSessionIssuanceResponse:
    """Create a new support session. Returns plaintext token exactly once."""
    db = _get_db()
    try:
        # granted_by_user_id: use admin key or fallback.
        granted_by = request.headers.get("X-Admin-Key-User", "admin")
        resp, token = create_session(db, body, granted_by_user_id=granted_by)

        # Emit elicitation event.
        from datetime import UTC, datetime
        _emit_elicitation_event(db, "support_session_granted", {
            "session_id": str(resp.id),
            "granted_to_email": resp.granted_to_email,
            "granted_at": resp.granted_at.isoformat(),
        })

        return SupportSessionIssuanceResponse(session=resp, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()


@router.get("/api/admin/support-sessions")
async def list_support_sessions(
    request: Request,
    cursor: UUID | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
) -> list[SupportSessionResponse]:
    """List all support sessions (paginated, newest first). Admin-key required."""
    _require_admin_key(request)
    db = _get_db()
    try:
        return list_sessions(db, cursor=cursor, limit=limit)
    finally:
        db.close()


@router.get("/api/admin/support-sessions/{session_id}")
async def get_support_session(
    session_id: UUID,
    request: Request,
) -> SupportSessionResponse:
    """Get a single support session. Admin-key required."""
    _require_admin_key(request)
    db = _get_db()
    try:
        result = get_session(db, session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="session not found")
        return result
    finally:
        db.close()


@router.post("/api/admin/support-sessions/{session_id}/revoke")
async def revoke_support_session(
    session_id: UUID,
    request: Request,
) -> SupportSessionResponse:
    """Revoke a support session. Idempotent."""
    db = _get_db()
    try:
        result = revoke_session(db, session_id, reason="admin_revoked")
        if result is None:
            raise HTTPException(status_code=404, detail="session not found")

        # Emit elicitation event.
        from datetime import UTC, datetime
        _emit_elicitation_event(db, "support_session_revoked", {
            "session_id": str(session_id),
            "revoked_at": (result.revoked_at.isoformat() if result.revoked_at else datetime.now(UTC).isoformat()),
        })

        return result
    finally:
        db.close()


@router.get("/api/admin/support-sessions/{session_id}/transcript")
async def get_support_session_transcript(
    session_id: UUID,
    request: Request,
) -> TranscriptResponse:
    """Get the audit-export transcript for a support session. Admin-key required."""
    _require_admin_key(request)
    db = _get_db()
    try:
        session_resp = get_session(db, session_id)
        if session_resp is None:
            raise HTTPException(status_code=404, detail="session not found")

        # Delegate to transcript_builder (CP6).
        try:
            from src.support.transcript_builder import build_transcript
            from src.graph.arcade_client import ArcadeClient
            from src.graph.config import ArcadeConfig
            from src.shared.config import get_settings  # Phase-9 fix
            client = ArcadeClient(
                config=ArcadeConfig.from_settings(get_settings())
            )
            transcript = await build_transcript(
                session_id=str(session_id),
                session_email=session_resp.granted_to_email,
                arcade_client=client,
            )
            return transcript
        except Exception:  # noqa: BLE001
            # ArcadeDB unavailable or other error — return empty transcript.
            from src.support.models import TranscriptSummary
            return TranscriptResponse(
                session_id=session_id,
                entries=[],
                summary=TranscriptSummary(
                    total_requests=0,
                    distinct_routes=0,
                ),
            )
    finally:
        db.close()


# --- Public route ---


@router.get("/api/support/status", response_model=SupportStatusResponse)
async def support_status() -> SupportStatusResponse:
    """Public status: is there an active support session?

    Returns ``{active: bool, email: str|null, expires_at: str|null}``.
    Explicit nulls when inactive — never absent fields.
    """
    db = _get_db()
    try:
        return get_active_status(db)
    finally:
        db.close()
