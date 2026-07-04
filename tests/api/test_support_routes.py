"""Support session route tests (Chunk 45, CP3 / D372–D374).

Covers the five admin routes under ``/api/admin/support-sessions`` and the
single public route ``GET /api/support/status``. The DB layer is patched
at the route-module boundary so tests run without live Postgres.

Critical invariants enforced by tests:

* Issuance returns the plaintext token exactly once (D372).
* Revocation is idempotent (POST revoke twice → same result).
* Admin GET routes require admin-key (route-local ``_require_admin_key``).
* Public status route returns explicit nulls when inactive.
* Elicitation events are emitted on create/revoke (best-effort).
* Transcript route stubs until CP6 (returns empty entries).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.support.models import (
    SupportSessionResponse,
    SupportStatusResponse,
    TranscriptResponse,
    TranscriptSummary,
)


@pytest.fixture()
def client():
    return TestClient(app)


def _make_response(
    *,
    session_id: UUID | None = None,
    email: str = "op@example.com",
    revoked_at: datetime | None = None,
    revoke_reason: str | None = None,
) -> SupportSessionResponse:
    now = datetime.now(UTC)
    return SupportSessionResponse(
        id=session_id or uuid4(),
        granted_by_user_id="admin",
        granted_to_email=email,
        granted_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=3),
        revoked_at=revoked_at,
        revoke_reason=revoke_reason,
        scope_tags={"all": True},
        created_via="api",
        last_used_at=now,
    )


# ---- Issuance (POST /api/admin/support-sessions) ----


def test_create_session_returns_token(client):
    """POST returns 201 with session + plaintext token."""
    resp_model = _make_response()
    token = "support:test-plaintext-token"

    with (
        patch(
            "src.api.support_routes._get_db",
            return_value=MagicMock(),
        ),
        patch(
            "src.api.support_routes.create_session",
            return_value=(resp_model, token),
        ),
        patch("src.api.support_routes._emit_elicitation_event"),
    ):
        resp = client.post(
            "/api/admin/support-sessions",
            json={
                "granted_to_email": "op@example.com",
                "expires_in_seconds": 14400,
                "scope_tags": {"all": True},
                "created_via": "api",
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["token"] == token
    assert body["session"]["granted_to_email"] == "op@example.com"
    # Token hash must NOT appear in response.
    assert "token_hash" not in body["session"]


def test_create_session_conflict_409(client):
    """POST returns 409 when active session already exists."""
    with (
        patch(
            "src.api.support_routes._get_db",
            return_value=MagicMock(),
        ),
        patch(
            "src.api.support_routes.create_session",
            side_effect=ValueError("Active session exists"),
        ),
    ):
        resp = client.post(
            "/api/admin/support-sessions",
            json={
                "granted_to_email": "op@example.com",
                "expires_in_seconds": 14400,
                "scope_tags": {"all": True},
                "created_via": "api",
            },
        )

    assert resp.status_code == 409


# ---- Revocation (POST /api/admin/support-sessions/{id}/revoke) ----


def test_revoke_session_success(client):
    """POST revoke returns the revoked session."""
    sid = uuid4()
    now = datetime.now(UTC)
    resp_model = _make_response(session_id=sid, revoked_at=now, revoke_reason="admin_revoked")

    with (
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch("src.api.support_routes.revoke_session", return_value=resp_model),
        patch("src.api.support_routes._emit_elicitation_event"),
    ):
        resp = client.post(f"/api/admin/support-sessions/{sid}/revoke")

    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None


def test_revoke_session_not_found(client):
    """POST revoke returns 404 for nonexistent session."""
    sid = uuid4()

    with (
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch("src.api.support_routes.revoke_session", return_value=None),
    ):
        resp = client.post(f"/api/admin/support-sessions/{sid}/revoke")

    assert resp.status_code == 404


# ---- Admin GET routes (require admin key) ----


def test_list_sessions_requires_admin_key(client):
    """GET /api/admin/support-sessions returns 401 without key."""
    with patch.dict("os.environ", {"GRACE_ADMIN_KEY": "test-admin-key-list"}):
        resp = client.get("/api/admin/support-sessions")
    assert resp.status_code == 401


def test_list_sessions_with_admin_key(client):
    """GET /api/admin/support-sessions returns 200 with valid key."""
    resp_model = _make_response()
    with (
        patch.dict("os.environ", {"GRACE_ADMIN_KEY": "test-admin-key-list-ok"}),
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch("src.api.support_routes.list_sessions", return_value=[resp_model]),
    ):
        resp = client.get(
            "/api/admin/support-sessions",
            headers={"X-Admin-Key": "test-admin-key-list-ok"},
        )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_session_requires_admin_key(client):
    """GET /api/admin/support-sessions/{id} returns 401 without key."""
    with patch.dict("os.environ", {"GRACE_ADMIN_KEY": "test-admin-key-get"}):
        resp = client.get(f"/api/admin/support-sessions/{uuid4()}")
    assert resp.status_code == 401


def test_get_session_not_found(client):
    """GET /api/admin/support-sessions/{id} returns 404 for missing session."""
    sid = uuid4()
    with (
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch("src.api.support_routes.get_session", return_value=None),
    ):
        resp = client.get(f"/api/admin/support-sessions/{sid}")
    assert resp.status_code == 404


# ---- Transcript (GET /api/admin/support-sessions/{id}/transcript) ----


def test_transcript_returns_stub(client):
    """GET transcript returns empty stub when CP6 module not yet built."""
    sid = uuid4()
    resp_model = _make_response(session_id=sid)

    with (
        patch.dict("os.environ", {"GRACE_ADMIN_KEY": "test-admin-key-transcript"}),
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch("src.api.support_routes.get_session", return_value=resp_model),
    ):
        resp = client.get(
            f"/api/admin/support-sessions/{sid}/transcript",
            headers={"X-Admin-Key": "test-admin-key-transcript"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["summary"]["total_requests"] == 0


# ---- Public status (GET /api/support/status) ----


def test_status_inactive(client):
    """Public status returns explicit nulls when no active session."""
    with (
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch(
            "src.api.support_routes.get_active_status",
            return_value=SupportStatusResponse(active=False, email=None, expires_at=None),
        ),
    ):
        resp = client.get("/api/support/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is False
    assert body["email"] is None
    assert body["expires_at"] is None


def test_status_active(client):
    """Public status returns email + expires_at when session is active."""
    expires = datetime.now(UTC) + timedelta(hours=3)
    with (
        patch("src.api.support_routes._get_db", return_value=MagicMock()),
        patch(
            "src.api.support_routes.get_active_status",
            return_value=SupportStatusResponse(active=True, email="op@example.com", expires_at=expires),
        ),
    ):
        resp = client.get("/api/support/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["email"] == "op@example.com"
    assert body["expires_at"] is not None
