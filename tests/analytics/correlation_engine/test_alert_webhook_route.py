"""Alert webhook tests (Chunk 33, D249/D254/D162).

Three tests:
1. Keyed-loopback POST writes ``alert_events`` row + increments counter.
2. Non-loopback (non-bridge) POST returns 403.
3. D162 cardinality guard: > 20 distinct alertnames fold into ``_other_``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api import auth_middleware as auth_mw_module
from src.api import analytics_routes as analytics_routes_module
from src.api.main import app


@pytest.fixture
def set_admin_key(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(auth_mw_module, "GRACE_ADMIN_KEY", value)

    return _set


@pytest.fixture
def loopback_client():
    """In-process TestClient — request.client.host == 'testclient' which is
    in LOOPBACK_HOSTS for both auth-middleware and the alert-webhook
    source-IP guard.
    """
    return TestClient(app)


@pytest.fixture
def remote_client():
    return TestClient(app, client=("8.8.8.8", 12345))


@pytest.fixture
def reset_alertname_cap(monkeypatch):
    """Each test starts with an empty alertname-cap dict."""
    monkeypatch.setattr(analytics_routes_module, "_alertname_seen", {})


@pytest.fixture
def reset_engine_cache(monkeypatch, test_engine):
    monkeypatch.setattr(analytics_routes_module, "_engine_cache", test_engine)


@pytest.fixture
def cleanup_alert_events(test_engine):
    # ISS-0003: clean BEFORE as well as after — the route's 60s payload-hash
    # dedup reads alert_events, so residue from any earlier suite test that
    # posted the identical default payload flips this test's write into
    # duplicate_ignored (order-dependent full-suite failure, passes alone).
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM alert_events"))
    yield
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM alert_events"))


def _payload(alertname: str = "TestAlert") -> dict:
    return {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "severity": "warning",
                    "ontology_module": "finance",
                },
                "annotations": {"summary": "test"},
                "startsAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ]
    }


def test_keyed_loopback_post_writes_alert_event(
    loopback_client,
    set_admin_key,
    reset_engine_cache,
    reset_alertname_cap,
    cleanup_alert_events,
    test_engine,
):
    """Loopback POST + valid X-Admin-Key → 200 + DB row + counter increment."""
    set_admin_key("supersecret")
    resp = loopback_client.post(
        "/api/analytics/alerts/_internal",
        headers={"X-Admin-Key": "supersecret"},
        json=_payload(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["written"] == 1

    with test_engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM alert_events")).scalar()
    assert n == 1


def test_non_loopback_post_returns_403(remote_client, set_admin_key):
    """A non-loopback / non-bridge source IP is rejected with 403."""
    set_admin_key("supersecret")
    resp = remote_client.post(
        "/api/analytics/alerts/_internal",
        headers={"X-Admin-Key": "supersecret"},
        json=_payload(),
    )
    assert resp.status_code == 403


def test_alertname_cardinality_guard_top_n_plus_other(
    loopback_client,
    set_admin_key,
    reset_engine_cache,
    reset_alertname_cap,
    cleanup_alert_events,
    test_engine,
):
    """D162 guard: > 20 distinct alertnames cause subsequent ones to fold to _other_."""
    set_admin_key("supersecret")

    # Send 25 distinct alertnames; the cap is 20.
    for i in range(25):
        resp = loopback_client.post(
            "/api/analytics/alerts/_internal",
            headers={"X-Admin-Key": "supersecret"},
            json=_payload(alertname=f"DistinctAlert_{i}"),
        )
        assert resp.status_code == 200, resp.text

    seen = analytics_routes_module._alertname_seen
    # First 20 are present; the rest get bucketed as _other_ at metric time.
    assert len(seen) == 20
    # Confirm the cap function returns _other_ for a 21st-and-beyond name.
    assert analytics_routes_module._capped_alertname("DistinctAlert_99") == "_other_"
    # And known alertnames still pass through.
    assert (
        analytics_routes_module._capped_alertname("DistinctAlert_0")
        == "DistinctAlert_0"
    )
