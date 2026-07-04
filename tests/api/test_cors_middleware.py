"""Tests for the dev-origin CORS middleware (D205, Chunk 27 remediation).

FastAPI does not auto-expose OPTIONS on POST-only routes, so without
CORSMiddleware a browser preflight gets 405. This suite locks the
dev-origin allowlist contract. Chunk 31 will replace it with the
hardened production CORS policy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from src.api.main import app

    return TestClient(app)


def _preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-graph-scope",
    }


def test_preflight_localhost_3000_allowed(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight_headers("http://localhost:3000"),
    )
    assert resp.status_code == 200
    assert (
        resp.headers.get("access-control-allow-origin")
        == "http://localhost:3000"
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods
    allow_headers = resp.headers.get(
        "access-control-allow-headers", ""
    ).lower()
    assert "x-graph-scope" in allow_headers
    assert "content-type" in allow_headers


def test_preflight_127_0_0_1_3000_allowed(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight_headers("http://127.0.0.1:3000"),
    )
    assert resp.status_code == 200
    assert (
        resp.headers.get("access-control-allow-origin")
        == "http://127.0.0.1:3000"
    )


def test_preflight_disallowed_origin_rejected(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight_headers("http://evil.example.com"),
    )
    # Starlette's CORSMiddleware returns 400 with the failure text for a
    # disallowed preflight origin; we assert the ACL header is not echoed
    # to be tolerant across versions.
    assert (
        resp.headers.get("access-control-allow-origin")
        != "http://evil.example.com"
    )


def test_simple_get_from_allowed_origin_carries_cors_header(client):
    resp = client.get(
        "/api/regeneration/config",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 200
    assert (
        resp.headers.get("access-control-allow-origin")
        == "http://localhost:3000"
    )
