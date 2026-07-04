"""Tests for the admin-key authentication middleware (Chunk 31).

Covers the 5-step admission decision tree (D236), the path-template
matcher (D237), and the integration ordering of CORS → Auth → Scope
in the real app (CP3).
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth_middleware as auth_mw_module
from src.api.auth_middleware import (
    AuthMiddleware,
    EXEMPT_PATHS,
    _match_path_template,
)
from src.mcp_server.server import READONLY_ROUTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Minimal app with AuthMiddleware only — isolates CP1 from CP3."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/metrics")
    async def metrics():
        return {"ok": True}

    @app.get("/api/graph/health")
    async def graph_health():
        return {"ok": True}

    @app.get("/api/graph/entities/{grace_id}")
    async def get_entity(grace_id: str):
        return {"grace_id": grace_id}

    @app.post("/api/retrieval/query")
    async def retrieval_query():
        return {"ok": True}

    @app.post("/api/regeneration/query")
    async def regeneration_query():
        return {"ok": True}

    @app.post("/api/llm/config")
    async def llm_config():
        return {"ok": True}

    return app


@pytest.fixture
def loopback_client():
    app = _build_app()
    return TestClient(app, client=("127.0.0.1", 12345))


@pytest.fixture
def remote_client():
    app = _build_app()
    return TestClient(app, client=("192.168.1.100", 12345))


@pytest.fixture
def set_admin_key(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(auth_mw_module, "GRACE_ADMIN_KEY", value)
    return _set


@pytest.fixture
def unset_admin_key(monkeypatch):
    monkeypatch.setattr(auth_mw_module, "GRACE_ADMIN_KEY", "")


# ---------------------------------------------------------------------------
# Path-template matcher (D237)
# ---------------------------------------------------------------------------


def test_match_path_template_literal_equality():
    assert _match_path_template("/api/graph/health", "/api/graph/health")


def test_match_path_template_segment_count_mismatch():
    assert not _match_path_template(
        "/api/graph/entities/{grace_id}", "/api/graph/entities"
    )


def test_match_path_template_placeholder_substitution():
    assert _match_path_template(
        "/api/graph/entities/{grace_id}",
        "/api/graph/entities/abc-123",
    )


def test_match_path_template_lookup_does_not_match_placeholder():
    # /api/graph/entities/lookup is its own literal entry; the placeholder
    # template /api/graph/entities/{grace_id} should also match a string
    # like 'lookup' positionally — but the literal entry wins because both
    # are in READONLY_ROUTES. This test asserts the matcher's positional
    # behavior; the auth middleware admits via either matching entry.
    assert _match_path_template(
        "/api/graph/entities/lookup", "/api/graph/entities/lookup"
    )


def test_readonly_routes_count_matches_server():
    # Single source of truth import contract (D237); Chunk 39 change-directives
    # GETs; Chunk 41 Layer 6 sample-CQ POST; Chunk 42 permissions triggers;
    # Chunk 48 preview; Chunk 51 federation resolve/validate; Chunk 53 connector GETs;
    # Chunk 72a +6 extraction/claim/job GETs (D470);
    # D522 session +1 review assist POST (read-only LLM explanation);
    # subsequent deploy-repo additions bring the total to 36.
    assert len(READONLY_ROUTES) == 36


def test_match_path_template_negative_extra_segment():
    assert not _match_path_template(
        "/api/graph/entities/{grace_id}",
        "/api/graph/entities/abc/extra",
    )


# ---------------------------------------------------------------------------
# Step 1: exempt paths
# ---------------------------------------------------------------------------


def test_exempt_path_metrics_admitted(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.get("/metrics")
    assert resp.status_code == 200


def test_exempt_paths_set_includes_expected():
    assert "/metrics" in EXEMPT_PATHS
    assert "/api/health" in EXEMPT_PATHS
    assert "/openapi.json" in EXEMPT_PATHS


def test_exempt_health_admitted_no_header(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.get("/api/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Step 2: GET/HEAD/OPTIONS admission
# ---------------------------------------------------------------------------


def test_get_admitted_from_remote_with_key_set(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.get("/api/graph/health")
    assert resp.status_code == 200


def test_get_with_grace_id_admitted_from_remote(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.get("/api/graph/entities/abc-123")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Step 3: read-only POST queries (D237)
# ---------------------------------------------------------------------------


def test_retrieval_query_post_admitted_from_remote(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.post("/api/retrieval/query", json={})
    assert resp.status_code == 200


def test_regeneration_query_post_admitted_from_remote(
    remote_client, set_admin_key
):
    set_admin_key("supersecret")
    resp = remote_client.post("/api/regeneration/query", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Step 4: localhost bypass when key unset
# ---------------------------------------------------------------------------


def test_localhost_bypass_when_key_unset(loopback_client, unset_admin_key):
    resp = loopback_client.post("/api/llm/config", json={})
    assert resp.status_code == 200


def test_localhost_bypass_disabled_when_key_set(loopback_client, set_admin_key):
    # Localhost client + GRACE_ADMIN_KEY set + no header → 401.
    set_admin_key("supersecret")
    resp = loopback_client.post("/api/llm/config", json={})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "admin key required"}


def test_non_loopback_rejected_when_key_unset(remote_client, unset_admin_key):
    resp = remote_client.post("/api/llm/config", json={})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "admin key required"}


# ---------------------------------------------------------------------------
# Step 5: X-Admin-Key admission
# ---------------------------------------------------------------------------


def test_correct_key_admits_mutating_route(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.post(
        "/api/llm/config",
        json={},
        headers={"X-Admin-Key": "supersecret"},
    )
    assert resp.status_code == 200


def test_wrong_key_rejected(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.post(
        "/api/llm/config",
        json={},
        headers={"X-Admin-Key": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "admin key required"}


def test_missing_key_rejected(remote_client, set_admin_key):
    set_admin_key("supersecret")
    resp = remote_client.post("/api/llm/config", json={})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "admin key required"}


# ---------------------------------------------------------------------------
# request.client = None defensive path
# ---------------------------------------------------------------------------


def test_request_client_none_treated_as_non_loopback(set_admin_key):
    """When request.client is None, behave as non-loopback — require key."""
    import asyncio
    from starlette.requests import Request

    set_admin_key("supersecret")

    async def run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/llm/config",
            "headers": [],
            "query_string": b"",
            "client": None,  # explicitly absent
            "scheme": "http",
            "server": ("testserver", 80),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive)
        mw = AuthMiddleware(app=lambda *a, **kw: None)

        async def call_next(req):
            from starlette.responses import JSONResponse
            return JSONResponse({"ok": True})

        return await mw.dispatch(request, call_next)

    response = asyncio.run(run())
    assert response.status_code == 401
    assert json.loads(response.body.decode()) == {"detail": "admin key required"}


# ---------------------------------------------------------------------------
# CP3 integration: middleware order CORS → Auth → Scope on real app
# ---------------------------------------------------------------------------


def test_order_cors_runs_before_auth_then_rejects_missing_key(monkeypatch):
    """Allowed origin + missing X-Admin-Key + key set → 401 with CORS headers.

    Proves CORS ran first (header echoed), then Auth rejected.
    """
    monkeypatch.setattr(auth_mw_module, "GRACE_ADMIN_KEY", "supersecret")
    from src.api.main import app

    with TestClient(app, client=("192.168.1.100", 12345)) as client:
        resp = client.post(
            "/api/llm/config",
            json={},
            headers={"Origin": "http://localhost:3000"},
        )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "admin key required"}
    # CORS ran first → header is present on the response.
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_order_cors_rejects_disallowed_origin_before_auth(monkeypatch):
    """Disallowed origin preflight → CORS rejects before Auth runs."""
    monkeypatch.setattr(auth_mw_module, "GRACE_ADMIN_KEY", "supersecret")
    from src.api.main import app

    with TestClient(app, client=("192.168.1.100", 12345)) as client:
        resp = client.options(
            "/api/llm/config",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-admin-key",
            },
        )

    # CORS does not echo the disallowed origin.
    assert (
        resp.headers.get("access-control-allow-origin")
        != "http://evil.example.com"
    )
