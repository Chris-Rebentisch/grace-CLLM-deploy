"""API tests for Chunk 28 D212 graph list routes.

Covers:
  - Default page size (25).
  - limit cap enforcement (limit > 100 → 422).
  - Cursor round-trip across pages.
  - Empty-scope behavior.
  - Route order: /entities list NOT shadowed by parametric /entities/{grace_id}.
  - Relationships list smoke.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient as HttpxTestClient

from src.api.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(grace_id: str, entity_type: str = "Legal_Entity", **extra):
    base = {
        "grace_id": grace_id,
        "@type": entity_type,
        "name": f"entity-{grace_id[-4:]}",
        "ontology_module": "legal_entity",
        "_deprecated": False,
        "human_validated": False,
    }
    base.update(extra)
    return base


def _rel_row(grace_id: str, source: str, target: str, rel_type: str = "owns"):
    return {
        "source_grace_id": source,
        "target_grace_id": target,
        "relationship_type": rel_type,
        "r": {
            "grace_id": grace_id,
            "@type": rel_type,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/graph/entities — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_entities_default_page_size_is_25():
    """Default limit is 25; when exactly 25 results come back, next_cursor is null."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        # 25 rows exactly — limit+1 = 26 was requested, we return 25 → no more.
        mock_client.execute_cypher.return_value = {
            "result": [{"n": _node(f"g-{i:04d}")} for i in range(25)]
        }

        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/graph/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entities"]) == 25
        assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_entities_limit_over_100_returns_422():
    """Pydantic Query(le=100) enforces the cap at route level."""
    transport = ASGITransport(app=app)
    async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/graph/entities?limit=200")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_entities_cursor_round_trip():
    """Page 1 returns a cursor; page 2 request with that cursor resolves."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # First call returns limit+1 = 6 rows (limit=5 → more available)
        first_page = {"result": [{"n": _node(f"g-{i:04d}")} for i in range(6)]}
        # Second call returns limit = 5 rows, indicating last page
        second_page = {"result": [{"n": _node(f"h-{i:04d}")} for i in range(5)]}
        mock_client.execute_cypher.side_effect = [first_page, second_page]

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/entities?limit=5")
            assert resp.status_code == 200
            body = resp.json()
            cursor = body["next_cursor"]
            assert cursor is not None
            assert len(body["entities"]) == 5

            resp2 = await ac.get(f"/api/graph/entities?limit=5&cursor={cursor}")
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert len(body2["entities"]) == 5
            assert body2["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_entities_empty_scope():
    """Empty DB returns {entities: [], next_cursor: null}."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {"result": []}

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"entities": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_list_entities_route_order_not_shadowed_by_parametric():
    """The list route must resolve; a subsequent GET for a specific id still works.

    Verified via the route definition order in `app.routes`, plus a live
    request to each variant.
    """
    paths = [(r.path, tuple(sorted(r.methods))) for r in app.routes
             if r.path.startswith("/api/graph/entities") and "GET" in getattr(r, "methods", set())]
    list_idx = [p for p, _ in paths].index("/api/graph/entities")
    param_idx = [p for p, _ in paths].index("/api/graph/entities/{grace_id}")
    # List must be registered before parametric to guarantee routing.
    assert list_idx < param_idx

    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        # List returns empty; subsequent GET for a random id also returns None
        mock_client.execute_cypher.return_value = {"result": []}

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            list_resp = await ac.get("/api/graph/entities")
            assert list_resp.status_code == 200
            param_resp = await ac.get("/api/graph/entities/some-uuid")
            assert param_resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/graph/relationships — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_relationships_basic():
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {
            "result": [_rel_row(f"r-{i:04d}", f"a-{i}", f"b-{i}") for i in range(3)]
        }

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/relationships?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["relationships"]) == 3
        assert body["next_cursor"] is None
        first = body["relationships"][0]
        assert first["relationship_type"] == "owns"
        assert first["source_grace_id"] == "a-0"
        assert first["target_grace_id"] == "b-0"
