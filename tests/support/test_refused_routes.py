"""Tests for @no_support_session decorator + frozenset (Chunk 45, CP4, D373).

Validates belt-side enforcement: import-time assertion crash for
unregistered tuples, request-time 403 for support sessions, and
pass-through for normal requests.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.support.refused_routes import (
    BLOCKED_FROM_SUPPORT_SESSION_ROUTES,
    no_support_session,
)


def test_frozenset_has_4_entries():
    """BLOCKED_FROM_SUPPORT_SESSION_ROUTES contains exactly 4 tuples."""
    assert len(BLOCKED_FROM_SUPPORT_SESSION_ROUTES) == 4


def test_frozenset_entries():
    """Verify the exact 4 blocked tuples."""
    expected = {
        ("POST", "/api/llm/config"),
        ("POST", "/api/llm/config/test"),
        ("POST", "/api/ontology/ratify"),
        ("POST", "/api/permissions/matrix/ratify"),
    }
    assert BLOCKED_FROM_SUPPORT_SESSION_ROUTES == expected


def test_decorator_assertion_crash_on_unregistered():
    """@no_support_session crashes at import time for unknown tuple."""
    with pytest.raises(AssertionError, match="not in BLOCKED_FROM_SUPPORT_SESSION_ROUTES"):
        @no_support_session("POST", "/api/nonexistent/route")
        async def fake_handler():
            pass


def test_decorator_blocks_support_session():
    """Decorated route returns 403 when support_session_id is set."""
    from src.api.main import app

    client = TestClient(app)

    import src.api.auth_middleware as mw
    orig = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-key-decorator-block"
    orig_remote = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = True
    try:
        import hashlib
        from src.support.models import SupportSession
        from datetime import UTC, datetime, timedelta

        token = "support:decorator-test-token"
        now = datetime.now(UTC)
        session = SupportSession(
            id="00000000-0000-0000-0000-000000000001",
            granted_by_user_id="admin",
            granted_to_email="op@example.com",
            granted_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=3),
            scope_tags={"all": True},
            created_via="api",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            last_used_at=now,
        )

        with patch(
            "src.api.auth_middleware._lookup_support_session",
            return_value=session,
        ):
            resp = client.post(
                "/api/llm/config",
                json={
                    "provider": "ollama",
                    "model": "qwen2.5:7b",
                    "base_url": "http://localhost:11434",
                    "timeout": 30,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403
            assert "blocked for support sessions" in resp.json()["detail"]
    finally:
        mw.GRACE_ADMIN_KEY = orig
        mw.GRACE_REMOTE_ACCESS_ENABLED = orig_remote


def test_decorator_passes_without_support_session():
    """Decorated route works normally when no support session is active."""
    from src.api.main import app

    client = TestClient(app)
    # With no admin key and localhost, the route should be admitted.
    import src.api.auth_middleware as mw
    orig = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = ""
    try:
        resp = client.post(
            "/api/llm/config",
            json={
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "base_url": "http://localhost:11434",
                "timeout": 30,
            },
        )
        # Should reach the handler (200 or some non-403 response).
        assert resp.status_code != 403
    finally:
        mw.GRACE_ADMIN_KEY = orig


def test_all_four_routes_decorated():
    """All 4 blocked routes return 403 via decorator for support sessions."""
    from src.api.main import app

    client = TestClient(app)

    import src.api.auth_middleware as mw
    orig = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-key-all-four-decorator"
    orig_remote = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = True

    try:
        import hashlib
        from src.support.models import SupportSession
        from datetime import UTC, datetime, timedelta

        token = "support:all-four-decorator"
        now = datetime.now(UTC)
        session = SupportSession(
            id="00000000-0000-0000-0000-000000000002",
            granted_by_user_id="admin",
            granted_to_email="op@example.com",
            granted_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=3),
            scope_tags={"all": True},
            created_via="api",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            last_used_at=now,
        )

        paths = [
            "/api/llm/config",
            "/api/llm/config/test",
            "/api/ontology/ratify",
            "/api/permissions/matrix/ratify",
        ]

        for path in paths:
            with patch(
                "src.api.auth_middleware._lookup_support_session",
                return_value=session,
            ):
                resp = client.post(
                    path,
                    json={},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 403, (
                    f"Expected 403 for {path}, got {resp.status_code}"
                )
    finally:
        mw.GRACE_ADMIN_KEY = orig
        mw.GRACE_REMOTE_ACCESS_ENABLED = orig_remote
