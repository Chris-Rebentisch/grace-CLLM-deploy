"""Tests for the X-Graph-Scope logging middleware (Chunk 27, D194)."""

from __future__ import annotations

import pytest
import structlog
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from src.api.main import app

    return TestClient(app)


def test_scope_header_is_logged_when_provided(client):
    with structlog.testing.capture_logs() as captured:
        resp = client.get("/metrics", headers={"X-Graph-Scope": "all"})
    assert resp.status_code == 200
    events = [
        e for e in captured if e.get("event") == "scope.request_received"
    ]
    assert events, f"scope.request_received not captured: {captured!r}"
    assert events[-1]["scope"] == "all"


def test_scope_header_defaults_to_all_when_missing(client):
    with structlog.testing.capture_logs() as captured:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    events = [
        e for e in captured if e.get("event") == "scope.request_received"
    ]
    assert events
    # Default is "all" because no header was sent.
    assert all(e["scope"] == "all" for e in events)


# ---------- Chunk 29 D229: multi-segment parsing + injection guard ----------


def test_segments_syntax_parses():
    """segments:m1,m2,... syntax returns correct scope_type and segment list."""
    from src.api.scope_middleware import _parse_scope

    scope_type, segments, error = _parse_scope("segments:finance,legal")
    assert scope_type == "segments"
    assert segments == ["finance", "legal"]
    assert error is None

    scope_type, segments, error = _parse_scope("segment:finance")
    assert scope_type == "segment"
    assert segments == ["finance"]
    assert error is None

    scope_type, segments, error = _parse_scope("all")
    assert scope_type == "all"
    assert segments is None
    assert error is None


def test_injection_guard_on_segment_names():
    """Segment names with SQL injection patterns are rejected."""
    from src.api.scope_middleware import _parse_scope

    scope_type, segments, error = _parse_scope("segments:malformed; DROP TABLE")
    assert error is not None
    assert "Invalid segment name" in error

    scope_type, segments, error = _parse_scope("segments:")
    assert error is not None
