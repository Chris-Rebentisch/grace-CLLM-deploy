"""CP1 — Verify explicit /metrics route returns 200 without redirect (D458)."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_metrics_explicit_route_returns_200_without_redirect():
    """GET /metrics returns 200, not 307."""
    resp = client.get("/metrics", follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_metrics_mount_still_serves_subpath():
    """GET /metrics/ still returns 200 via the mount (backward compatibility)."""
    resp = client.get("/metrics/", follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_metrics_content_type_plain_text():
    """Response Content-Type starts with text/plain and body contains Prometheus exposition markers."""
    resp = client.get("/metrics")
    content_type = resp.headers.get("content-type", "")
    assert content_type.startswith("text/plain"), f"Expected text/plain, got {content_type}"
    body = resp.text
    # Prometheus exposition format always contains at least one HELP or TYPE line
    assert "# HELP" in body or "# TYPE" in body, "Body lacks Prometheus exposition markers"
