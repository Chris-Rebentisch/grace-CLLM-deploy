"""Support session lifecycle manager (Chunk 45, D372).

Token generation, SHA-256 hashing, session creation with concurrent-
session cap, revocation, and active-session lookup.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.support.models import (
    SupportSession,
    SupportSessionCreate,
    SupportSessionResponse,
    SupportStatusResponse,
)

log = structlog.get_logger()

SUPPORT_TOKEN_PREFIX = "support:"


def generate_token() -> str:
    """Generate a new support token with the ``support:`` prefix (D372).

    Format: ``support:<base64url(32-byte random)>``.
    """
    raw = secrets.token_urlsafe(32)
    return f"{SUPPORT_TOKEN_PREFIX}{raw}"


def hash_token(token: str) -> str:
    """SHA-256 hash of the full token string (D372)."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    db: Session,
    request: SupportSessionCreate,
    granted_by_user_id: str,
) -> tuple[SupportSessionResponse, str]:
    """Create a new support session.

    Returns ``(response, plaintext_token)``. The plaintext token is
    returned exactly once; subsequent reads never include it.

    Enforces single-active-session cap:
    1. ``SELECT ... FOR UPDATE`` to lock existing non-revoked rows.
    2. Auto-revoke any expired-but-not-revoked sessions.
    3. If an unexpired, non-revoked session still exists, raise.

    Raises:
        ValueError: if an active (non-expired, non-revoked) session
            already exists.
    """
    now = datetime.now(UTC)

    # Lock existing non-revoked sessions for the duration of this tx.
    rows = db.execute(
        text(
            "SELECT id, expires_at FROM support_sessions "
            "WHERE revoked_at IS NULL "
            "FOR UPDATE"
        )
    ).fetchall()

    for row in rows:
        session_id, expires_at = row
        if expires_at <= now:
            # Auto-revoke expired session.
            db.execute(
                text(
                    "UPDATE support_sessions "
                    "SET revoked_at = :now, revoke_reason = 'auto_expired' "
                    "WHERE id = :sid"
                ),
                {"now": now, "sid": session_id},
            )
            log.info(
                "support.session.auto_revoked",
                session_id=str(session_id),
                reason="expired",
            )
        else:
            raise ValueError(
                f"Active session {session_id} already exists; revoke it first."
            )

    # Generate token and hash.
    plaintext_token = generate_token()
    token_hash_val = hash_token(plaintext_token)
    expires_at = now + timedelta(seconds=request.expires_in_seconds)

    try:
        result = db.execute(
            text(
                "INSERT INTO support_sessions "
                "(granted_by_user_id, granted_to_email, granted_at, "
                " expires_at, scope_tags, created_via, token_hash) "
                "VALUES (:granted_by, :email, :granted_at, :expires_at, "
                "        CAST(:scope_tags AS jsonb), :created_via, :token_hash) "
                "RETURNING id, granted_by_user_id, granted_to_email, "
                "          granted_at, expires_at, revoked_at, revoke_reason, "
                "          scope_tags, created_via, last_used_at"
            ),
            {
                "granted_by": granted_by_user_id,
                "email": request.granted_to_email,
                "granted_at": now,
                "expires_at": expires_at,
                "scope_tags": _jsonb_dumps(request.scope_tags),
                "created_via": request.created_via,
                "token_hash": token_hash_val,
            },
        )
        row = result.fetchone()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "Concurrent session already exists (unique constraint)."
        ) from exc

    response = SupportSessionResponse(
        id=row[0],
        granted_by_user_id=row[1],
        granted_to_email=row[2],
        granted_at=row[3],
        expires_at=row[4],
        revoked_at=row[5],
        revoke_reason=row[6],
        scope_tags=row[7] if isinstance(row[7], dict) else {"all": True},
        created_via=row[8],
        last_used_at=row[9],
    )
    return response, plaintext_token


def revoke_session(
    db: Session,
    session_id: UUID,
    reason: str | None = None,
) -> SupportSessionResponse | None:
    """Revoke a support session by setting ``revoked_at``.

    Idempotent: if already revoked, returns the existing state.
    Returns ``None`` if the session does not exist.
    """
    now = datetime.now(UTC)

    row = db.execute(
        text(
            "SELECT id, revoked_at FROM support_sessions WHERE id = :sid"
        ),
        {"sid": session_id},
    ).fetchone()

    if row is None:
        return None

    if row[1] is None:
        db.execute(
            text(
                "UPDATE support_sessions "
                "SET revoked_at = :now, revoke_reason = :reason "
                "WHERE id = :sid"
            ),
            {"now": now, "reason": reason or "admin_revoked", "sid": session_id},
        )
        db.commit()

    return get_session(db, session_id)


def get_session(
    db: Session,
    session_id: UUID,
) -> SupportSessionResponse | None:
    """Fetch a single session by ID. Returns ``None`` if not found."""
    row = db.execute(
        text(
            "SELECT id, granted_by_user_id, granted_to_email, "
            "       granted_at, expires_at, revoked_at, revoke_reason, "
            "       scope_tags, created_via, last_used_at "
            "FROM support_sessions WHERE id = :sid"
        ),
        {"sid": session_id},
    ).fetchone()

    if row is None:
        return None

    return SupportSessionResponse(
        id=row[0],
        granted_by_user_id=row[1],
        granted_to_email=row[2],
        granted_at=row[3],
        expires_at=row[4],
        revoked_at=row[5],
        revoke_reason=row[6],
        scope_tags=row[7] if isinstance(row[7], dict) else {"all": True},
        created_via=row[8],
        last_used_at=row[9],
    )


def list_sessions(
    db: Session,
    cursor: UUID | None = None,
    limit: int = 25,
) -> list[SupportSessionResponse]:
    """List sessions with cursor pagination (newest first)."""
    limit = min(limit, 100)
    if cursor:
        rows = db.execute(
            text(
                "SELECT id, granted_by_user_id, granted_to_email, "
                "       granted_at, expires_at, revoked_at, revoke_reason, "
                "       scope_tags, created_via, last_used_at "
                "FROM support_sessions "
                "WHERE granted_at < (SELECT granted_at FROM support_sessions WHERE id = :cursor) "
                "ORDER BY granted_at DESC "
                "LIMIT :limit"
            ),
            {"cursor": cursor, "limit": limit},
        ).fetchall()
    else:
        rows = db.execute(
            text(
                "SELECT id, granted_by_user_id, granted_to_email, "
                "       granted_at, expires_at, revoked_at, revoke_reason, "
                "       scope_tags, created_via, last_used_at "
                "FROM support_sessions "
                "ORDER BY granted_at DESC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        ).fetchall()

    return [
        SupportSessionResponse(
            id=r[0],
            granted_by_user_id=r[1],
            granted_to_email=r[2],
            granted_at=r[3],
            expires_at=r[4],
            revoked_at=r[5],
            revoke_reason=r[6],
            scope_tags=r[7] if isinstance(r[7], dict) else {"all": True},
            created_via=r[8],
            last_used_at=r[9],
        )
        for r in rows
    ]


def lookup_by_token_hash(
    db: Session,
    token_hash_val: str,
) -> SupportSession | None:
    """Look up an active session by token hash.

    Returns ``None`` if no matching non-expired, non-revoked session.
    On match, refreshes ``last_used_at``.
    """
    now = datetime.now(UTC)
    row = db.execute(
        text(
            "SELECT id, granted_by_user_id, granted_to_email, "
            "       granted_at, expires_at, revoked_at, revoke_reason, "
            "       scope_tags, created_via, token_hash, last_used_at "
            "FROM support_sessions "
            "WHERE token_hash = :hash "
            "  AND revoked_at IS NULL "
            "  AND expires_at > :now"
        ),
        {"hash": token_hash_val, "now": now},
    ).fetchone()

    if row is None:
        return None

    session_id = row[0]

    # Refresh last_used_at.
    db.execute(
        text(
            "UPDATE support_sessions SET last_used_at = :now WHERE id = :sid"
        ),
        {"now": now, "sid": session_id},
    )
    db.commit()

    return SupportSession(
        id=row[0],
        granted_by_user_id=row[1],
        granted_to_email=row[2],
        granted_at=row[3],
        expires_at=row[4],
        revoked_at=row[5],
        revoke_reason=row[6],
        scope_tags=row[7] if isinstance(row[7], dict) else {"all": True},
        created_via=row[8],
        token_hash=row[9],
        last_used_at=now,
    )


def get_active_status(db: Session) -> SupportStatusResponse:
    """Public status check: is there an active support session?

    Returns ``{active: bool, email: str|null, expires_at: str|null}``.
    """
    now = datetime.now(UTC)
    row = db.execute(
        text(
            "SELECT granted_to_email, expires_at "
            "FROM support_sessions "
            "WHERE revoked_at IS NULL AND expires_at > :now "
            "LIMIT 1"
        ),
        {"now": now},
    ).fetchone()

    if row is None:
        return SupportStatusResponse(active=False, email=None, expires_at=None)

    return SupportStatusResponse(
        active=True,
        email=row[0],
        expires_at=row[1],
    )


def _jsonb_dumps(obj: dict) -> str:
    """Serialize dict to JSON string for Postgres JSONB insertion."""
    import json
    return json.dumps(obj)
