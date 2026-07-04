"""API endpoint tests for entity/relationship CRUD (Chunk 13)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient as HttpxTestClient

from src.api.main import app


@pytest.fixture(autouse=True)
def _pin_type_enforcement_off(monkeypatch):
    """ISS-0003: these are HTTP-contract tests, not type-enforcement tests.

    The F-09 write-time allowlist is TTL-cached and built from whatever
    ontology an EARLIER suite test ratified into grace_test — so 'Person'
    422s or passes depending on suite order (passes in isolation). Pin
    enforcement off and drop the cache both sides; enforcement behavior has
    its own dedicated tests.
    """
    from src.graph.type_enforcement import invalidate_type_cache

    monkeypatch.setenv("GRACE_TYPE_ENFORCEMENT", "off")
    invalidate_type_cache()
    yield
    invalidate_type_cache()


# ===========================================================================
# POST /api/graph/entities/
# ===========================================================================


@pytest.mark.asyncio
async def test_create_entity_endpoint():
    """POST /api/graph/entities/ returns 200 with EntityCreateResponse."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # canonical_lookup miss + CREATE success
        mock_client.execute_cypher.side_effect = [
            {"result": []},  # canonical miss
            {"result": [{"@rid": "#1:0", "grace_id": "new-uuid"}]},  # CREATE
        ]

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/graph/entities/",
                json={"entity_type": "Person", "properties": {"name": "Alice"}},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is True
        assert data["entity_type"] == "Person"
        assert "grace_id" in data


# ===========================================================================
# GET /api/graph/entities/{grace_id}
# ===========================================================================


@pytest.mark.asyncio
async def test_get_entity_endpoint():
    """GET /api/graph/entities/{grace_id} returns 200."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {
            "result": [{"@rid": "#1:0", "grace_id": "uuid-1", "name": "Alice"}]
        }

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/entities/uuid-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Alice"


@pytest.mark.asyncio
async def test_get_entity_not_found():
    """GET /api/graph/entities/{grace_id} returns 404 when not found."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {"result": []}

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/entities/nonexistent")
        assert resp.status_code == 404


# ===========================================================================
# PUT /api/graph/entities/{grace_id}
# ===========================================================================


@pytest.mark.asyncio
async def test_update_entity_endpoint():
    """PUT /api/graph/entities/{grace_id} returns 200."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {
            "result": [{"@rid": "#1:0", "grace_id": "uuid-1", "name": "Bob"}]
        }

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                "/api/graph/entities/uuid-1",
                json={"properties": {"name": "Bob"}},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bob"


# ===========================================================================
# POST /api/graph/entities/bulk
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_insert_endpoint():
    """POST /api/graph/entities/bulk returns 200 with BulkInsertResponse."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # One entity: canonical miss + CREATE success
        mock_client.execute_cypher.side_effect = [
            {"result": []},  # canonical miss
            {"result": [{"@rid": "#1:0"}]},  # CREATE
        ]

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/graph/entities/bulk",
                json={
                    "entities": [
                        {"entity_type": "Person", "properties": {"name": "Alice"}}
                    ]
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entities_created"] == 1


# ===========================================================================
# GET /api/graph/entities/lookup
# ===========================================================================


@pytest.mark.asyncio
async def test_lookup_entity_endpoint():
    """GET /api/graph/entities/lookup?type=X&name=Y returns 200."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # canonical_lookup hit + get_entity
        mock_client.execute_cypher.side_effect = [
            {"result": [{"n.grace_id": "uuid-1"}]},  # canonical hit
            {"result": [{"@rid": "#1:0", "grace_id": "uuid-1", "name": "Alice"}]},  # get
        ]

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/graph/entities/lookup",
                params={"type": "Person", "name": "Alice"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["grace_id"] == "uuid-1"


# ===========================================================================
# POST /api/graph/relationships/
# ===========================================================================


@pytest.mark.asyncio
async def test_create_relationship_endpoint():
    """POST /api/graph/relationships/ returns 200."""
    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_client.execute_cypher.return_value = {
            "result": [{"@rid": "#2:0", "grace_id": "edge-uuid"}]
        }

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/graph/relationships/",
                json={
                    "relationship_type": "owns",
                    "source_grace_id": "src-uuid",
                    "target_grace_id": "tgt-uuid",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["relationship_type"] == "owns"
        assert "grace_id" in data
