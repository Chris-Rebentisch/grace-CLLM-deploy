"""Concurrent revocation race condition tests (Chunk 45, D376 failure mode 4).

Tests that concurrent revocation requests don't corrupt session state.
Worst case: one request is admitted after revocation.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from src.shared.database import get_session_factory
from src.support.models import SupportSessionCreate
from src.support.session_manager import (
    create_session,
    hash_token,
    lookup_by_token_hash,
    revoke_session,
)


@pytest.fixture
def db_session():
    factory = get_session_factory()
    session = factory()
    yield session
    session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
    session.execute(text("DELETE FROM support_sessions"))
    session.commit()
    session.close()


def test_revoke_then_lookup_returns_none(db_session):
    """After revocation, token lookup returns None."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, token = create_session(db_session, req, granted_by_user_id="admin")
    token_h = hash_token(token)

    # Revoke.
    revoke_session(db_session, resp.id, reason="revoked")

    # Lookup should return None.
    found = lookup_by_token_hash(db_session, token_h)
    assert found is None


def test_concurrent_revocation_idempotent(db_session):
    """Two concurrent revocations of the same session both succeed (idempotent)."""
    req = SupportSessionCreate(granted_to_email="op@example.com")
    resp, _ = create_session(db_session, req, granted_by_user_id="admin")

    # First revocation.
    r1 = revoke_session(db_session, resp.id, reason="first")
    assert r1 is not None
    assert r1.revoked_at is not None

    # Second revocation (simulating concurrent request).
    r2 = revoke_session(db_session, resp.id, reason="second")
    assert r2 is not None
    # revoked_at should match first revocation (idempotent).
    assert r2.revoked_at == r1.revoked_at
    # revoke_reason stays from first revocation.
    assert r2.revoke_reason == "first"
