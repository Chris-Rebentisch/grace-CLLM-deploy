"""D450 per-route preflight integration tests (Chunk 66).

One test per PATCH / DELETE / PUT route.  Each fires ``OPTIONS`` with
browser-origin headers and asserts the preflight response admits the verb.

Routes with path parameters use sentinel UUID
``00000000-0000-0000-0000-000000000000`` — CORS middleware responds
before routing, so the UUID value is irrelevant (spec §18.1).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.api.main import app

_SENTINEL = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _preflight_headers(verb: str) -> dict[str, str]:
    return {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": verb,
    }


# ---- PATCH routes (9) ----


@pytest.mark.parametrize(
    "path",
    [
        "/api/ontology/daemon/kill-switch",
        "/api/ontology/calibration/config/1",
        f"/api/recon/documented-reality/schedules/{_SENTINEL}",
        f"/api/change-directives/{_SENTINEL}",
        f"/api/change-directives/{_SENTINEL}/criteria/{_SENTINEL}",
        f"/api/ingestion/sources/{_SENTINEL}",
        "/api/ingestion/config/deployment-path",
        "/api/ingestion/config/organization-domains",
        "/api/ingestion/config/tier3-threshold",
    ],
    ids=[
        "kill-switch",
        "calibration-config",
        "dr-schedule",
        "change-directive",
        "change-directive-criterion",
        "ingestion-source",
        "ingestion-deployment-path",
        "ingestion-org-domains",
        "ingestion-tier3-threshold",
    ],
)
def test_preflight_PATCH(client, path):
    resp = client.options(path, headers=_preflight_headers("PATCH"))
    assert resp.status_code == 200, f"OPTIONS {path} returned {resp.status_code}"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "PATCH" in allow_methods, (
        f"OPTIONS {path}: Access-Control-Allow-Methods={allow_methods!r} "
        f"does not include PATCH"
    )


# ---- DELETE routes (3) ----


@pytest.mark.parametrize(
    "path",
    [
        "/api/graph/management/namespaces/test-ns",
        f"/api/ingestion/sources/{_SENTINEL}",
        f"/api/federation/namespaces/{_SENTINEL}",
    ],
    ids=[
        "graph-namespace",
        "ingestion-source",
        "federation-namespace",
    ],
)
def test_preflight_DELETE(client, path):
    resp = client.options(path, headers=_preflight_headers("DELETE"))
    assert resp.status_code == 200, f"OPTIONS {path} returned {resp.status_code}"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allow_methods, (
        f"OPTIONS {path}: Access-Control-Allow-Methods={allow_methods!r} "
        f"does not include DELETE"
    )


# ---- PUT routes (3) ----


@pytest.mark.parametrize(
    "path",
    [
        f"/api/graph/entities/{_SENTINEL}",
        f"/api/discovery/cqs/{_SENTINEL}",
        f"/api/discovery/cqs/{_SENTINEL}/status",
    ],
    ids=[
        "graph-entity",
        "discovery-cq",
        "discovery-cq-status",
    ],
)
def test_preflight_PUT(client, path):
    resp = client.options(path, headers=_preflight_headers("PUT"))
    assert resp.status_code == 200, f"OPTIONS {path} returned {resp.status_code}"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "PUT" in allow_methods, (
        f"OPTIONS {path}: Access-Control-Allow-Methods={allow_methods!r} "
        f"does not include PUT"
    )
