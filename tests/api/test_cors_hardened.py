"""Tests for hardened CORS env-driven allowlist (Chunk 31, D238)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Import at module top so src.api.main is fully loaded BEFORE any
# monkeypatch.setenv fires. The CORSMiddleware on the live app is bound
# at first import; if we let the import happen lazily inside a test
# that has already mutated GRACE_CORS_ORIGINS, the live app's allowlist
# would freeze to that test's value and break sibling test files.
from src.api.main import _parse_cors_origins, app  # noqa: F401


# ---------------------------------------------------------------------------
# _parse_cors_origins() unit tests
# ---------------------------------------------------------------------------


def test_parse_cors_single_origin(monkeypatch):
    monkeypatch.setenv("GRACE_CORS_ORIGINS", "http://example.com")
    assert _parse_cors_origins() == ["http://example.com"]


def test_parse_cors_multi_origin_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv(
        "GRACE_CORS_ORIGINS",
        " http://a.example.com , http://b.example.com ,",
    )
    assert _parse_cors_origins() == [
        "http://a.example.com",
        "http://b.example.com",
    ]


def test_parse_cors_dev_fallback_when_unset(monkeypatch, capsys):
    monkeypatch.delenv("GRACE_CORS_ORIGINS", raising=False)
    result = _parse_cors_origins()
    assert result == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_parse_cors_empty_string_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GRACE_CORS_ORIGINS", "")
    result = _parse_cors_origins()
    assert result == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_parse_cors_trailing_comma_filtered(monkeypatch):
    monkeypatch.setenv("GRACE_CORS_ORIGINS", "http://example.com,,,")
    assert _parse_cors_origins() == ["http://example.com"]


# ---------------------------------------------------------------------------
# Live preflight + headers (uses dev fallback origins)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    from src.api.main import app
    return TestClient(app)


def _preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-admin-key",
    }


def test_preflight_includes_x_admin_key_in_allow_headers(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight("http://localhost:3000"),
    )
    assert resp.status_code == 200
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "x-admin-key" in allow_headers


def test_preflight_rejects_non_allowlisted_origin(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight("http://evil.example.com"),
    )
    assert (
        resp.headers.get("access-control-allow-origin")
        != "http://evil.example.com"
    )


def test_allow_credentials_remains_false(client):
    resp = client.options(
        "/api/regeneration/query",
        headers=_preflight("http://localhost:3000"),
    )
    # CORSMiddleware only emits the credentials header when allow_credentials=True.
    assert "access-control-allow-credentials" not in {
        k.lower() for k in resp.headers.keys()
    }
