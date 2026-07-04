"""Tests for PermissionMatrixMiddleware (Chunk 42, D334 / R7)."""

from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.permissions.api_middleware import PermissionMatrixMiddleware
from src.permissions.enforcer import get_enforcer, rebuild_enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)


@pytest.fixture(autouse=True)
def _reset_enforcer():
    """Reset the enforcer + force enforcement ON for these enforcement tests.

    Enforcement is opt-in and OFF by default (single-operator onboarding), so
    these tests — which assert deny/allow behavior — must explicitly enable it.
    """
    import os

    prev = os.environ.get("GRACE_PERMISSION_ENFORCEMENT_ENABLED")
    os.environ["GRACE_PERMISSION_ENFORCEMENT_ENABLED"] = "1"
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)
    if prev is None:
        os.environ.pop("GRACE_PERMISSION_ENFORCEMENT_ENABLED", None)
    else:
        os.environ["GRACE_PERMISSION_ENFORCEMENT_ENABLED"] = prev


def _make_app(matrix: PermissionMatrix | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(PermissionMatrixMiddleware)
    rebuild_enforcer(matrix)

    @app.post("/api/permissions/matrix/ratify")
    async def ratify():
        return {"ok": True}

    @app.post("/api/change-directives")
    async def cd_create():
        return {"ok": True}

    @app.get("/api/permissions/matrix/active")
    async def active():
        return {"ok": True}

    @app.post("/api/retrieval/query")
    async def retrieval_query():
        # Simulates a read-only POST listed in READONLY_ROUTES.
        return {"ok": True}

    return app


def test_readonly_get_passes_through_with_no_matrix() -> None:
    app = _make_app(matrix=None)
    client = TestClient(app)
    resp = client.get("/api/permissions/matrix/active")
    assert resp.status_code == 200


def test_mutating_route_with_no_matrix_returns_403() -> None:
    app = _make_app(matrix=None)
    client = TestClient(app)
    resp = client.post("/api/permissions/matrix/ratify", json={})
    assert resp.status_code == 403
    body = resp.json()
    assert body["reason"] == "no_active_matrix"


def test_readonly_post_in_allowlist_passes_through() -> None:
    # /api/retrieval/query is on READONLY_ROUTES (D237) — read-only POST.
    app = _make_app(matrix=None)
    client = TestClient(app)
    resp = client.post("/api/retrieval/query", json={"query_text": "x"})
    assert resp.status_code == 200


def test_explicit_allow_in_matrix_admits_mutation() -> None:
    user_id = uuid4()
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="c1",
                display_name="ratifiers",
                members=[RoleClusterMember(person_grace_id=str(user_id))],
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="/api/permissions/matrix/ratify",
                        action="ratify",
                        decision="allow",
                    )
                ],
            )
        ],
        default_decision="deny",
    )
    app = _make_app(matrix=matrix)

    # Inject a fake admission outcome by setting request.state.user_id
    # via a custom middleware below the permission middleware.
    @app.middleware("http")
    async def fake_admission(request, call_next):
        request.state.user_id = str(user_id)
        return await call_next(request)

    client = TestClient(app)
    resp = client.post("/api/permissions/matrix/ratify", json={})
    assert resp.status_code == 200


def test_explicit_deny_overrides_allow() -> None:
    user_id = uuid4()
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="c1",
                display_name="ratifiers",
                members=[RoleClusterMember(person_grace_id=str(user_id))],
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="/api/permissions/matrix/ratify",
                        action="ratify",
                        decision="deny",
                    ),
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="/api/permissions/matrix/ratify",
                        action="ratify",
                        decision="allow",
                    ),
                ],
            )
        ],
        default_decision="allow",
    )
    app = _make_app(matrix=matrix)

    @app.middleware("http")
    async def fake_admission(request, call_next):
        request.state.user_id = str(user_id)
        return await call_next(request)

    client = TestClient(app)
    resp = client.post("/api/permissions/matrix/ratify", json={})
    assert resp.status_code == 403
    assert resp.json()["reason"] == "explicit_deny"


def test_main_app_registers_permission_after_auth() -> None:
    """Verify registration order: PermissionMatrixMiddleware on inbound
    runs AFTER AuthMiddleware (R7)."""
    main = importlib.import_module("src.api.main")
    middleware_classes = [
        m.cls.__name__ for m in main.app.user_middleware  # type: ignore[attr-defined]
    ]
    # user_middleware is in registration order (last-added first per
    # Starlette). Auth was added AFTER Permission so Auth appears at a
    # smaller index than Permission. On inbound (reverse), Auth runs
    # first then Permission — exactly what we want for R7.
    assert "AuthMiddleware" in middleware_classes
    assert "PermissionMatrixMiddleware" in middleware_classes
    assert middleware_classes.index("AuthMiddleware") < middleware_classes.index(
        "PermissionMatrixMiddleware"
    )


def test_enforcement_disabled_passes_through_gated_route(monkeypatch):
    """With enforcement OFF (default), a gated mutating route is NOT blocked even
    when no matrix is active — the single-operator onboarding bypass."""
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "0")
    rebuild_enforcer(None)  # no active matrix
    app = _make_app(matrix=None)
    client = TestClient(app)
    # /api/permissions/matrix/ratify is gated; with enforcement off it passes.
    resp = client.post("/api/permissions/matrix/ratify")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_enforcement_enabled_still_denies_without_matrix(monkeypatch):
    """With enforcement ON and no matrix, the gated route is denied (403)."""
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "1")
    rebuild_enforcer(None)
    app = _make_app(matrix=None)
    client = TestClient(app)
    resp = client.post("/api/change-directives")
    assert resp.status_code == 403
