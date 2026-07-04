"""Auth middleware step 5 (support-token bearer) tests (Chunk 45, D372).

Tests the new support-token admission step inserted between step 4
(localhost bypass) and step 6 (admin-key).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.support.models import SupportSession


def _make_session(
    *,
    token: str = "support:test-token-abc",
) -> SupportSession:
    """Create a mock SupportSession."""
    now = datetime.now(UTC)
    return SupportSession(
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


@pytest.fixture
def _enable_remote_access():
    """Enable GRACE_REMOTE_ACCESS_ENABLED for the test."""
    import src.api.auth_middleware as mw
    original = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = True
    yield
    mw.GRACE_REMOTE_ACCESS_ENABLED = original


@pytest.fixture
def _disable_remote_access():
    """Disable GRACE_REMOTE_ACCESS_ENABLED for the test."""
    import src.api.auth_middleware as mw
    original = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = False
    yield
    mw.GRACE_REMOTE_ACCESS_ENABLED = original


@pytest.fixture
def client():
    from src.api.main import app
    with TestClient(app) as c:
        yield c


def _patch_lookup(return_value):
    """Patch the support session lookup helper in auth_middleware."""
    return patch(
        "src.api.auth_middleware._lookup_support_session",
        return_value=return_value,
    )


def test_feature_flag_off_falls_through(_disable_remote_access, client):
    """When GRACE_REMOTE_ACCESS_ENABLED=false, support tokens are ignored."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = ""
    try:
        resp = client.post(
            "/api/elicitation/events",
            json={},
            headers={"Authorization": "Bearer support:fake-token"},
        )
        # Should get 422 (validation error) not 401 — step 4 localhost bypass.
        assert resp.status_code == 422
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_valid_token_admits(_enable_remote_access, client):
    """Valid support token admits the request and sets request.state."""
    token = "support:valid-test-token-xyz"
    session = _make_session(token=token)

    with _patch_lookup(session):
        resp = client.post(
            "/api/elicitation/events",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 422 = validation error = request was admitted past auth.
        assert resp.status_code == 422


def test_expired_token_falls_through(_enable_remote_access, client):
    """Expired token → lookup returns None → falls through to step 6 → 401."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-admin-key-123"
    try:
        with _patch_lookup(None):
            resp = client.post(
                "/api/elicitation/events",
                json={},
                headers={"Authorization": "Bearer support:expired-token"},
            )
            assert resp.status_code == 401
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_revoked_token_falls_through(_enable_remote_access, client):
    """Revoked token → lookup returns None → falls through to step 6."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-admin-key-123"
    try:
        with _patch_lookup(None):
            resp = client.post(
                "/api/elicitation/events",
                json={},
                headers={"Authorization": "Bearer support:revoked-token"},
            )
            assert resp.status_code == 401
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_blocked_route_returns_403(_enable_remote_access, client):
    """Support session + blocked route → 403."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    # Set admin key so step 4 localhost bypass does NOT short-circuit.
    mw.GRACE_ADMIN_KEY = "test-key-for-blocked"
    try:
        token = "support:blocked-test-token"
        session = _make_session(token=token)

        with _patch_lookup(session):
            resp = client.post(
                "/api/llm/config",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403
            assert "blocked for support sessions" in resp.json()["detail"]
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_non_support_bearer_falls_through(_enable_remote_access, client):
    """Non-``support:`` bearer → falls through to step 6."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-admin-key-123"
    try:
        resp = client.post(
            "/api/elicitation/events",
            json={},
            headers={"Authorization": "Bearer some-other-token"},
        )
        assert resp.status_code == 401
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_existing_admin_key_still_works(_enable_remote_access, client):
    """Existing admin-key path (step 6) is unchanged after step 5 insertion."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-admin-key-regression"
    try:
        resp = client.post(
            "/api/elicitation/events",
            json={},
            headers={"X-Admin-Key": "test-admin-key-regression"},
        )
        assert resp.status_code == 422
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_existing_readonly_still_works(_enable_remote_access, client):
    """Read-only verbs (step 2) are still admitted."""
    resp = client.get("/api/graph/info")
    assert resp.status_code != 401


def test_existing_writable_review_unchanged(_enable_remote_access, client):
    """Writable review routes (step 3b) still fall through correctly."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = ""
    try:
        from src.mcp_server.server import WRITABLE_REVIEW_ROUTES
        if WRITABLE_REVIEW_ROUTES:
            method, path = next(iter(WRITABLE_REVIEW_ROUTES))
            resp = client.post(path, json={})
            assert resp.status_code != 401
    finally:
        mw.GRACE_ADMIN_KEY = orig_key


def test_frozenset_importable():
    """BLOCKED_FROM_SUPPORT_SESSION_ROUTES is importable with 4 entries."""
    from src.support.refused_routes import BLOCKED_FROM_SUPPORT_SESSION_ROUTES
    assert len(BLOCKED_FROM_SUPPORT_SESSION_ROUTES) == 4


def test_support_token_blocked_routes_all_four(_enable_remote_access, client):
    """All 4 blocked routes return 403 when accessed with support token."""
    import src.api.auth_middleware as mw
    orig_key = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-key-for-blocked-all"
    try:
        from src.support.refused_routes import BLOCKED_FROM_SUPPORT_SESSION_ROUTES

        token = "support:blocked-test-all"
        session = _make_session(token=token)

        for route_method, route_path in BLOCKED_FROM_SUPPORT_SESSION_ROUTES:
            with _patch_lookup(session):
                resp = client.post(
                    route_path,
                    json={},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 403, f"Expected 403 for {route_method} {route_path}"
    finally:
        mw.GRACE_ADMIN_KEY = orig_key
