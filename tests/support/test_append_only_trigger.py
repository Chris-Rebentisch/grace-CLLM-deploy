"""Append-only trigger contract tests for support_sessions (Chunk 45, D372).

Verifies: DELETE raises check_violation; UPDATE of immutable columns
raises check_violation; UPDATE of mutable columns (revoked_at,
revoke_reason, last_used_at) succeeds.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.shared.database import get_session_factory


@pytest.fixture
def db_session():
    """Create a DB session scoped to this test, with rollback cleanup."""
    factory = get_session_factory()
    session = factory()
    yield session
    session.rollback()
    session.close()


def _insert_test_session(session, *, token_suffix: str = "") -> str:
    """Insert a test support session and return its id."""
    token = f"support:{secrets.token_urlsafe(32)}{token_suffix}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(UTC)
    result = session.execute(
        text(
            "INSERT INTO support_sessions "
            "(granted_by_user_id, granted_to_email, granted_at, "
            " expires_at, scope_tags, created_via, token_hash, revoked_at) "
            "VALUES (:user, :email, :at, :exp, CAST(:tags AS jsonb), :via, :hash, :revoked) "
            "RETURNING id"
        ),
        {
            "user": "test-admin",
            "email": "operator@example.com",
            "at": now,
            "exp": now + timedelta(hours=4),
            "tags": '{"all": true}',
            "via": "api",
            "hash": token_hash,
            # Pre-revoke so the unique partial index doesn't conflict.
            "revoked": now,
        },
    )
    session_id = result.scalar()
    session.commit()
    return str(session_id)


def test_delete_blocked(db_session):
    """DELETE on support_sessions raises check_violation."""
    sid = _insert_test_session(db_session)
    with pytest.raises(IntegrityError, match="does not allow DELETE"):
        db_session.execute(
            text("DELETE FROM support_sessions WHERE id = :sid"),
            {"sid": sid},
        )
    db_session.rollback()


def test_update_immutable_column_id(db_session):
    """UPDATE of immutable column ``granted_by_user_id`` raises."""
    sid = _insert_test_session(db_session)
    with pytest.raises(IntegrityError, match="cannot modify immutable"):
        db_session.execute(
            text(
                "UPDATE support_sessions SET granted_by_user_id = 'changed' "
                "WHERE id = :sid"
            ),
            {"sid": sid},
        )
    db_session.rollback()


def test_update_immutable_column_email(db_session):
    """UPDATE of immutable column ``granted_to_email`` raises."""
    sid = _insert_test_session(db_session)
    with pytest.raises(IntegrityError, match="cannot modify immutable"):
        db_session.execute(
            text(
                "UPDATE support_sessions SET granted_to_email = 'changed@x.com' "
                "WHERE id = :sid"
            ),
            {"sid": sid},
        )
    db_session.rollback()


def test_update_immutable_column_token_hash(db_session):
    """UPDATE of immutable column ``token_hash`` raises."""
    sid = _insert_test_session(db_session)
    with pytest.raises(IntegrityError, match="cannot modify immutable"):
        db_session.execute(
            text(
                "UPDATE support_sessions SET token_hash = 'aaaa' "
                "WHERE id = :sid"
            ),
            {"sid": sid},
        )
    db_session.rollback()


def test_update_mutable_revoked_at(db_session):
    """UPDATE of mutable column ``revoked_at`` succeeds."""
    sid = _insert_test_session(db_session)
    now = datetime.now(UTC)
    db_session.execute(
        text(
            "UPDATE support_sessions SET revoked_at = :now WHERE id = :sid"
        ),
        {"now": now, "sid": sid},
    )
    db_session.commit()
    row = db_session.execute(
        text("SELECT revoked_at FROM support_sessions WHERE id = :sid"),
        {"sid": sid},
    ).fetchone()
    assert row[0] is not None


def test_update_mutable_revoke_reason(db_session):
    """UPDATE of mutable column ``revoke_reason`` succeeds."""
    sid = _insert_test_session(db_session)
    db_session.execute(
        text(
            "UPDATE support_sessions SET revoke_reason = 'test' WHERE id = :sid"
        ),
        {"sid": sid},
    )
    db_session.commit()
    row = db_session.execute(
        text("SELECT revoke_reason FROM support_sessions WHERE id = :sid"),
        {"sid": sid},
    ).fetchone()
    assert row[0] == "test"


def test_update_mutable_last_used_at(db_session):
    """UPDATE of mutable column ``last_used_at`` succeeds."""
    sid = _insert_test_session(db_session)
    now = datetime.now(UTC)
    db_session.execute(
        text(
            "UPDATE support_sessions SET last_used_at = :now WHERE id = :sid"
        ),
        {"now": now, "sid": sid},
    )
    db_session.commit()
    row = db_session.execute(
        text("SELECT last_used_at FROM support_sessions WHERE id = :sid"),
        {"sid": sid},
    ).fetchone()
    assert row[0] is not None
