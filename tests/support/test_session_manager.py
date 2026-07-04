"""Session manager tests (Chunk 45, D372).

Covers: token generation/hashing roundtrip, session creation, expiry cap/floor,
concurrent-session cap, expired-session auto-revocation, revocation
idempotency, active-session lookup, public status.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory
from src.support.models import SupportSessionCreate
from src.support.session_manager import (
    SUPPORT_TOKEN_PREFIX,
    create_session,
    generate_token,
    get_active_status,
    hash_token,
    lookup_by_token_hash,
    revoke_session,
)


@pytest.fixture
def db_session():
    factory = get_session_factory()
    session = factory()
    yield session
    # Clean up test rows to avoid partial-index collisions between tests.
    session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
    session.execute(text("DELETE FROM support_sessions"))
    session.commit()
    session.close()


def test_generate_token_prefix():
    """Token starts with ``support:`` prefix."""
    token = generate_token()
    assert token.startswith(SUPPORT_TOKEN_PREFIX)
    assert len(token) > len(SUPPORT_TOKEN_PREFIX) + 10


def test_hash_token_roundtrip():
    """SHA-256 hash of token is deterministic."""
    token = generate_token()
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert h1 == hashlib.sha256(token.encode()).hexdigest()


def test_create_session_returns_token_and_response(db_session):
    """create_session returns a response (without hash) and plaintext token."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, token = create_session(db_session, req, granted_by_user_id="admin")

    assert resp.granted_to_email == "op@example.com"
    assert resp.revoked_at is None
    assert token.startswith(SUPPORT_TOKEN_PREFIX)
    # Response model never has token_hash.
    assert not hasattr(resp, "token_hash") or "token_hash" not in resp.model_fields


def test_concurrent_session_cap(db_session):
    """Second non-revoked session raises ValueError."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    create_session(db_session, req, granted_by_user_id="admin")

    with pytest.raises(ValueError, match="Active session"):
        req2 = SupportSessionCreate(granted_to_email="op2@example.com")
        create_session(db_session, req2, granted_by_user_id="admin")


def test_expired_session_auto_revoked(db_session):
    """Expired-but-not-revoked session is auto-revoked on new creation."""
    now = datetime.now(UTC)
    # Insert a session that's already expired but not revoked.
    token_hash = hashlib.sha256(b"old-token").hexdigest()
    db_session.execute(
        text(
            "INSERT INTO support_sessions "
            "(granted_by_user_id, granted_to_email, granted_at, "
            " expires_at, scope_tags, created_via, token_hash) "
            "VALUES (:user, :email, :at, :exp, CAST(:tags AS jsonb), :via, :hash)"
        ),
        {
            "user": "admin",
            "email": "old@example.com",
            "at": now - timedelta(hours=5),
            "exp": now - timedelta(hours=1),  # expired 1 hour ago
            "tags": '{"all": true}',
            "via": "api",
            "hash": token_hash,
        },
    )
    db_session.commit()

    # New session should succeed (old one auto-revoked).
    req = SupportSessionCreate(granted_to_email="new@example.com")
    resp, _ = create_session(db_session, req, granted_by_user_id="admin")
    assert resp.granted_to_email == "new@example.com"

    # Old session should be revoked.
    old = db_session.execute(
        text("SELECT revoked_at, revoke_reason FROM support_sessions WHERE token_hash = :h"),
        {"h": token_hash},
    ).fetchone()
    assert old[0] is not None
    assert old[1] == "auto_expired"


def test_revocation_idempotent(db_session):
    """Revoking an already-revoked session is a no-op (no error)."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, _ = create_session(db_session, req, granted_by_user_id="admin")

    r1 = revoke_session(db_session, resp.id, reason="test")
    assert r1 is not None
    assert r1.revoked_at is not None

    r2 = revoke_session(db_session, resp.id, reason="second")
    assert r2 is not None
    # revoked_at unchanged from first revocation.
    assert r2.revoked_at == r1.revoked_at


def test_revoke_nonexistent_returns_none(db_session):
    """Revoking a non-existent session returns None."""
    import uuid
    result = revoke_session(db_session, uuid.uuid4())
    assert result is None


def test_lookup_by_token_hash_active(db_session):
    """lookup_by_token_hash returns session for valid, non-expired token."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, token = create_session(db_session, req, granted_by_user_id="admin")

    found = lookup_by_token_hash(db_session, hash_token(token))
    assert found is not None
    assert found.id == resp.id
    assert found.last_used_at is not None  # refreshed


def test_lookup_by_token_hash_revoked(db_session):
    """lookup_by_token_hash returns None for revoked token."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, token = create_session(db_session, req, granted_by_user_id="admin")
    revoke_session(db_session, resp.id)

    found = lookup_by_token_hash(db_session, hash_token(token))
    assert found is None


def test_get_active_status_active(db_session):
    """get_active_status returns active=True when a session exists."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    create_session(db_session, req, granted_by_user_id="admin")

    status = get_active_status(db_session)
    assert status.active is True
    assert status.email == "op@example.com"
    assert status.expires_at is not None


def test_get_active_status_inactive(db_session):
    """get_active_status returns active=False when no session exists."""
    status = get_active_status(db_session)
    assert status.active is False
    assert status.email is None
    assert status.expires_at is None


def test_expires_in_seconds_cap():
    """expires_in_seconds > 86400 is rejected by Pydantic."""
    with pytest.raises(Exception):
        SupportSessionCreate(
            granted_to_email="x@x.com", expires_in_seconds=86401
        )


def test_expires_in_seconds_floor():
    """expires_in_seconds < 3600 is rejected by Pydantic."""
    with pytest.raises(Exception):
        SupportSessionCreate(
            granted_to_email="x@x.com", expires_in_seconds=3599
        )
