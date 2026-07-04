"""Tests for the Documented Reality Report route surface (Chunk 37, D286).

Three tests:

1. On-demand generate happy path → 201 (mock pipeline returns small graph).
2. GET-latest happy + 404.
3. Schedule CRUD round-trip (POST create → GET list → PATCH update).
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.graph.arcade_client import get_arcade_client
from src.shared.database import get_engine


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_arcade_client():
    """Replace Arcade with an async mock returning a small populated graph
    so the empty-corpus carve-out does NOT fire (V=100 > floor=50)."""
    fake = AsyncMock()
    fake.execute_sql = AsyncMock(
        side_effect=lambda *_a, **_kw: _next_response(),
    )
    # Current flow (Phase-6 ArcadeDB fix): enumerate types via `schema:types`,
    # then one count query per type. Stateful generator returns the schema
    # enumeration first, then per-type counts (70 + 30 = 100 V > floor 50).
    responses = [
        {"result": [
            {"name": "Company", "type": "v"},
            {"name": "Person", "type": "v"},
            {"name": "works_at", "type": "e"},
        ]},
        {"result": [{"cnt": 70}]},   # Company
        {"result": [{"cnt": 30}]},   # Person
        {"result": [{"cnt": 50}]},   # works_at
    ]
    state = {"i": 0}

    def _next_response(*_a, **_kw):
        i = state["i"] % len(responses)
        state["i"] += 1
        return responses[i]

    fake.execute_sql = AsyncMock(side_effect=_next_response)
    app.dependency_overrides[get_arcade_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_arcade_client, None)


@pytest.fixture(autouse=True)
def _clean_dr_tables():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM recon_documented_reality_reports"))
        conn.execute(text("DELETE FROM recon_documented_reality_schedules"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM recon_documented_reality_reports"))
        conn.execute(text("DELETE FROM recon_documented_reality_schedules"))
        conn.commit()


@pytest.fixture(autouse=True)
def _ensure_admin_key_unset(monkeypatch):
    """Loopback bypass — these routes are admin-key gated when set."""
    monkeypatch.delenv("GRACE_ADMIN_KEY", raising=False)


# ---------------------------------------------------------------------------
# 1. On-demand generate happy path.
# ---------------------------------------------------------------------------


def test_generate_on_demand_returns_201_with_aggregations(client):
    resp = client.post("/api/recon/documented-reality/generate")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["trigger"] == "on_demand"
    assert body["corpus_below_floor"] is False
    assert body["aggregations"]["total_vertices"] == 100
    assert body["aggregations"]["total_edges"] == 50
    assert len(body["aggregations"]["top_entities"]) == 2


# ---------------------------------------------------------------------------
# 2. GET-latest happy + 404.
# ---------------------------------------------------------------------------


def test_get_latest_returns_404_then_200(client):
    miss = client.get("/api/recon/documented-reality/latest")
    assert miss.status_code == 404

    gen = client.post("/api/recon/documented-reality/generate")
    assert gen.status_code == 201

    got = client.get("/api/recon/documented-reality/latest")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["trigger"] == "on_demand"


def test_get_by_id_returns_404_for_unknown(client):
    unknown = uuid4()
    resp = client.get(f"/api/recon/documented-reality/{unknown}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Schedule CRUD round-trip.
# ---------------------------------------------------------------------------


def test_schedule_crud_round_trip(client):
    create = client.post(
        "/api/recon/documented-reality/schedules",
        json={"cadence": "monthly", "enabled": True},
    )
    assert create.status_code == 201, create.text
    created = create.json()
    sid = created["id"]
    assert created["cadence"] == "monthly"
    assert created["enabled"] is True

    listing = client.get("/api/recon/documented-reality/schedules")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert any(r["id"] == sid for r in rows)

    patch = client.patch(
        f"/api/recon/documented-reality/schedules/{sid}",
        json={"cadence": "quarterly", "enabled": False},
    )
    assert patch.status_code == 200, patch.text
    patched = patch.json()
    assert patched["id"] == sid
    assert patched["cadence"] == "quarterly"
    assert patched["enabled"] is False
