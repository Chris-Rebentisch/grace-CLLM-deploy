"""Phase 5.5 security posture closure walk (Chunk 46, D378.f, §30).

Twelve route-admission tests verifying cumulative Phase 5.5 (§20–§29)
security invariants:
- Mutating routes reject unauthenticated requests (401) when GRACE_ADMIN_KEY set.
- Read routes admit without admin key.
- BLOCKED_FROM_SUPPORT_SESSION_ROUTES returns 403 for support-session bearers.
- GRACE_REMOTE_ACCESS_ENABLED=false (default) causes support-token no-op.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.support.models import SupportSession


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from src.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def _admin_key_set():
    """Set GRACE_ADMIN_KEY so step 4 localhost bypass does NOT short-circuit."""
    import src.api.auth_middleware as mw

    orig = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = "test-posture-key-phase55"
    yield
    mw.GRACE_ADMIN_KEY = orig


@pytest.fixture
def _admin_key_unset():
    """Unset GRACE_ADMIN_KEY so localhost bypass applies."""
    import src.api.auth_middleware as mw

    orig = mw.GRACE_ADMIN_KEY
    mw.GRACE_ADMIN_KEY = ""
    yield
    mw.GRACE_ADMIN_KEY = orig


@pytest.fixture
def _enable_remote_access():
    import src.api.auth_middleware as mw

    orig = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = True
    yield
    mw.GRACE_REMOTE_ACCESS_ENABLED = orig


@pytest.fixture
def _disable_remote_access():
    import src.api.auth_middleware as mw

    orig = mw.GRACE_REMOTE_ACCESS_ENABLED
    mw.GRACE_REMOTE_ACCESS_ENABLED = False
    yield
    mw.GRACE_REMOTE_ACCESS_ENABLED = orig


def _make_support_session(token: str = "support:posture-test-token") -> SupportSession:
    now = datetime.now(UTC)
    return SupportSession(
        id=str(uuid4()),
        granted_by_user_id="admin",
        granted_to_email="op@example.com",
        granted_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=3),
        scope_tags={"all": True},
        created_via="api",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        last_used_at=now,
    )


def _patch_lookup(return_value):
    return patch(
        "src.api.auth_middleware._lookup_support_session",
        return_value=return_value,
    )


# ── Tests 1–5: Mutating routes without admin key → 401 ───────────────────


def test_recon_generate_gap_report_requires_admin_key(client, _admin_key_set):
    """POST /api/recon/gap-report/{session_id}/generate → 401 without key."""
    resp = client.post(f"/api/recon/gap-report/{uuid4()}/generate")
    assert resp.status_code == 401


def test_sensitivity_report_generate_requires_admin_key(client, _admin_key_set):
    """POST /api/sensitivity/report/generate → 401 without key."""
    resp = client.post("/api/sensitivity/report/generate", json={})
    assert resp.status_code == 401


def test_permissions_matrix_ratify_requires_admin_key(client, _admin_key_set):
    """POST /api/permissions/matrix/ratify → 401 without key."""
    resp = client.post("/api/permissions/matrix/ratify", json={})
    assert resp.status_code == 401


def test_decomposition_trigger_requires_admin_key(client, _admin_key_set):
    """POST /api/decomposition/runs/trigger → 401 without key."""
    resp = client.post("/api/decomposition/runs/trigger", json={})
    assert resp.status_code == 401


def test_change_directive_create_requires_admin_key(client, _admin_key_set):
    """POST /api/change-directives → 401 without key."""
    resp = client.post("/api/change-directives", json={})
    assert resp.status_code == 401


# ── Tests 6–8: Read routes admit without admin key ────────────────────────


def test_support_status_get_admitted(client, _admin_key_set):
    """GET /api/support/status → non-401 (public read path)."""
    resp = client.get("/api/support/status")
    assert resp.status_code != 401


def test_sensitivity_report_latest_get_admitted(client, _admin_key_set):
    """GET /api/sensitivity/report/latest → non-401."""
    resp = client.get("/api/sensitivity/report/latest")
    assert resp.status_code != 401


def test_permissions_matrix_active_get_admitted(client, _admin_key_set):
    """GET /api/permissions/matrix/active → non-401."""
    resp = client.get("/api/permissions/matrix/active")
    assert resp.status_code != 401


# ── Tests 9–10: Blocked routes with support-session bearer → 403 ─────────


def test_blocked_route_llm_config_403(
    client, _admin_key_set, _enable_remote_access
):
    """POST /api/llm/config with valid support bearer → 403."""
    token = "support:posture-blocked-llm"
    session = _make_support_session(token=token)
    with _patch_lookup(session):
        resp = client.post(
            "/api/llm/config",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert "blocked for support sessions" in resp.json()["detail"]


def test_blocked_route_permissions_ratify_403(
    client, _admin_key_set, _enable_remote_access
):
    """POST /api/permissions/matrix/ratify with valid support bearer → 403."""
    token = "support:posture-blocked-ratify"
    session = _make_support_session(token=token)
    with _patch_lookup(session):
        resp = client.post(
            "/api/permissions/matrix/ratify",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert "blocked for support sessions" in resp.json()["detail"]


# ── Test 11: BLOCKED_FROM_SUPPORT_SESSION_ROUTES has exactly 4 entries ────


def test_blocked_frozenset_has_four_entries():
    """§30.5: BLOCKED_FROM_SUPPORT_SESSION_ROUTES contains exactly 4 tuples."""
    from src.support.refused_routes import BLOCKED_FROM_SUPPORT_SESSION_ROUTES

    assert len(BLOCKED_FROM_SUPPORT_SESSION_ROUTES) == 4
    expected = {
        ("POST", "/api/llm/config"),
        ("POST", "/api/llm/config/test"),
        ("POST", "/api/ontology/ratify"),
        ("POST", "/api/permissions/matrix/ratify"),
    }
    assert BLOCKED_FROM_SUPPORT_SESSION_ROUTES == expected


# ── Test 12: GRACE_REMOTE_ACCESS_ENABLED=false → support-token no-op ─────


def test_remote_access_disabled_support_token_noop(
    client, _admin_key_unset, _disable_remote_access
):
    """When GRACE_REMOTE_ACCESS_ENABLED=false, support bearer is ignored.

    With admin key unset + localhost, the request falls through to step 4
    (localhost bypass) and is admitted — proving the support-token path was
    never entered.
    """
    resp = client.post(
        "/api/elicitation/events",
        json={},
        headers={"Authorization": "Bearer support:should-be-ignored"},
    )
    # 422 = validation error = request admitted past auth (step 4 bypass).
    assert resp.status_code == 422
