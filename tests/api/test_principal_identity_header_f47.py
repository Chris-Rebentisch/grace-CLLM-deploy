"""F-47 regression tests: X-Principal-Id header → per-principal sensitivity zones.

Before this fix, ``from_admission_tree()`` read ``request.state.user_id`` but
nothing ever set it — every retrieval request was an anonymous principal and
per-principal zones could never differentiate.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.auth_middleware import AuthMiddleware
from src.permissions.principal_context import from_admission_tree

_PERSON_ID = str(uuid4())
_captured: dict = {}


async def _probe(request):
    _captured["user"] = from_admission_tree(request)
    return JSONResponse({"ok": True})


@pytest.fixture()
def client():
    app = Starlette(routes=[Route("/probe", _probe, methods=["GET"])])
    app.add_middleware(AuthMiddleware)
    return TestClient(app)


def test_principal_header_reaches_from_admission_tree(client):
    _captured.clear()
    resp = client.get(
        "/probe",
        headers={
            "X-Principal-Id": _PERSON_ID,
            "X-Principal-Display-Name": "Diane Castellano",
        },
    )
    assert resp.status_code == 200
    user = _captured["user"]
    assert user.user_id == UUID(_PERSON_ID)
    assert user.display_name == "Diane Castellano"


def test_no_header_stays_anonymous(client):
    _captured.clear()
    resp = client.get("/probe")
    assert resp.status_code == 200
    assert _captured["user"].user_id is None


def test_malformed_principal_id_ignored(client):
    """A non-UUID assertion is dropped (logged), never a 500."""
    _captured.clear()
    resp = client.get("/probe", headers={"X-Principal-Id": "not-a-uuid"})
    assert resp.status_code == 200
    assert _captured["user"].user_id is None


def test_identity_header_grants_no_admission(client, monkeypatch):
    """The assertion selects a zone; it must NOT admit mutating requests."""
    import src.api.auth_middleware as am

    monkeypatch.setattr(am, "GRACE_ADMIN_KEY", "test-admin-key-value")

    app = Starlette(routes=[Route("/probe", _probe, methods=["POST"])])
    app.add_middleware(AuthMiddleware)
    c = TestClient(app)
    resp = c.post("/probe", headers={"X-Principal-Id": _PERSON_ID})
    assert resp.status_code == 401
