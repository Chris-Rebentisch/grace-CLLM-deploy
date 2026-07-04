"""CP2 — Auth middleware step-3b tests for writable review routes (D363).

Verifies:
- Writable review route with valid admin-key returns 200.
- Admin-key gate: writable review route without admin-key returns 401.
- Localhost bypass: writable review route from loopback when admin-key unset.
- Regression: existing step-3 read-only behavior unchanged.
- Regression: existing mutating routes still require admin-key.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _no_admin_key(monkeypatch):
    """Ensure no admin key is set."""
    monkeypatch.setattr(
        "src.api.auth_middleware.GRACE_ADMIN_KEY", ""
    )


@pytest.fixture
def _with_admin_key(monkeypatch):
    """Set a known admin key."""
    monkeypatch.setattr(
        "src.api.auth_middleware.GRACE_ADMIN_KEY", "test-key-abc123"
    )


@pytest.fixture
def client():
    from src.api.main import app

    with TestClient(app) as c:
        yield c


def test_writable_review_route_with_valid_admin_key(client, _with_admin_key):
    """Writable review route with valid admin-key is admitted to the route handler."""
    resp = client.post(
        "/api/elicitation/events",
        headers={"X-Admin-Key": "test-key-abc123"},
        json={},
    )
    # Should reach the route handler (may get 422 from Pydantic validation)
    # but NOT 401 from the middleware.
    assert resp.status_code != 401


def test_writable_review_route_without_admin_key_rejected(
    client, _with_admin_key
):
    """Writable review route without admin-key returns 401 when key is set."""
    resp = client.post(
        "/api/elicitation/events",
        json={},
    )
    assert resp.status_code == 401


def test_writable_review_route_wrong_admin_key_rejected(
    client, _with_admin_key
):
    """Writable review route with wrong admin-key returns 401."""
    resp = client.post(
        "/api/elicitation/events",
        headers={"X-Admin-Key": "wrong-key"},
        json={},
    )
    assert resp.status_code == 401


def test_writable_review_route_localhost_bypass(client, _no_admin_key):
    """Writable review route from loopback admitted when admin-key unset."""
    # TestClient scope["client"] is ("testclient", 50000) which is in LOOPBACK_HOSTS.
    resp = client.post(
        "/api/elicitation/events",
        json={},
    )
    # Should NOT be 401 — may get 422 from validation
    assert resp.status_code != 401


def test_writable_review_route_review_start_localhost(client, _no_admin_key):
    """Review start writable route admitted via localhost bypass."""
    resp = client.post(
        "/api/ontology/review/start",
        json={},
    )
    assert resp.status_code != 401


def test_writable_review_route_close_summary_localhost(client, _no_admin_key):
    """Close-summary writable route admitted via localhost bypass."""
    resp = client.post(
        "/api/regeneration/close-summary",
        json={},
    )
    assert resp.status_code != 401


# --- Regression tests ---


def test_readonly_route_bypasses_admin_key(client, _with_admin_key):
    """Read-only routes still bypass admin-key entirely (step-3 unchanged)."""
    resp = client.post(
        "/api/retrieval/query",
        json={"query_text": "test", "top_k": 1},
    )
    # Should NOT be 401 — read-only route admitted unconditionally.
    assert resp.status_code != 401


def test_get_request_bypasses_admin_key(client, _with_admin_key):
    """GET requests still bypass admin-key entirely (step-2 unchanged)."""
    resp = client.get("/api/ontology/active")
    assert resp.status_code != 401


def test_non_mcp_mutating_route_requires_admin_key(client, _with_admin_key):
    """Existing mutating routes still require admin-key (step-5 unchanged)."""
    resp = client.post(
        "/api/llm/config",
        json={},
    )
    assert resp.status_code == 401


def test_writable_review_decide_route_with_session_id(
    client, _no_admin_key
):
    """Writable review route with path template admitted via localhost bypass."""
    resp = client.post(
        "/api/ontology/review/00000000-0000-0000-0000-000000000001/decide",
        json={},
    )
    # Should NOT be 401
    assert resp.status_code != 401
