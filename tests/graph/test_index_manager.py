"""Tests for index management: static auto-indexes and dynamic requests."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.graph.index_manager import (
    DEFAULT_VERTEX_INDEXES,
    apply_pending_indexes,
    create_static_indexes,
    generate_index_ddl,
    store_index_request,
)
from src.graph.schema_sync_models import GraphIndexRequest


def test_static_indexes_created():
    """Default indexes include grace_id, name, valid_from, schema_version."""
    properties = [idx["property"] for idx in DEFAULT_VERTEX_INDEXES]
    assert "grace_id" in properties
    assert "name" in properties
    assert "valid_from" in properties
    assert "schema_version" in properties


@pytest.mark.asyncio
async def test_static_indexes_per_vertex_type():
    """Static indexes created for each vertex type in schema."""
    schema = {
        "entity_types": {
            "Person": {"properties": {"name": {"data_type": "string"}}},
            "Company": {"properties": {"name": {"data_type": "string"}}},
        },
    }
    client = AsyncMock()
    client.execute_sql = AsyncMock(return_value={"result": []})

    executed = await create_static_indexes(client, schema)

    # 4 indexes per type * 2 types = 8
    assert len(executed) == 8
    assert any("Person" in s and "grace_id" in s and "UNIQUE" in s for s in executed)
    assert any("Person" in s and "name" in s for s in executed)
    assert any("Company" in s and "valid_from" in s for s in executed)
    assert any("Person" in s and "schema_version" in s for s in executed)


def test_request_index_stored():
    """Pending index request is saved via store_index_request."""
    request = GraphIndexRequest(
        type_name="Person",
        property_name="jurisdiction",
        index_type="standard",
        reason="Frequent filtering by jurisdiction",
        requested_by="analytics",
    )
    mock_db = MagicMock()

    with patch("src.graph.index_manager.create_index_request") as mock_create:
        mock_create.return_value = request
        result = store_index_request(mock_db, request)

    assert result.type_name == "Person"
    assert result.property_name == "jurisdiction"
    assert result.status == "pending"
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_apply_pending_indexes():
    """Pending requests are executed against ArcadeDB and marked applied."""
    pending = [
        GraphIndexRequest(
            id=str(uuid4()),
            type_name="Person",
            property_name="jurisdiction",
            index_type="standard",
            reason="test",
            requested_by="analytics",
        ),
    ]

    client = AsyncMock()
    client.execute_sql = AsyncMock(return_value={"result": []})
    mock_db = MagicMock()

    with (
        patch("src.graph.index_manager.get_pending_index_requests", return_value=pending),
        patch("src.graph.index_manager.update_index_request_status"),
    ):
        results = await apply_pending_indexes(mock_db, client)

    assert len(results) == 1
    assert results[0].status == "applied"
    client.execute_sql.assert_called_once()


def test_generate_index_ddl():
    """Correct CREATE INDEX syntax generated."""
    ddl = generate_index_ddl("Legal_Entity", "name")
    assert ddl == "CREATE INDEX ON Legal_Entity (name)"

    ddl_unique = generate_index_ddl("Legal_Entity", "name", unique=True)
    assert ddl_unique == "CREATE INDEX ON Legal_Entity (name) UNIQUE"


@pytest.mark.asyncio
async def test_index_request_api():
    """POST /api/graph/request-index stores pending request."""
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    request_data = {
        "type_name": "Person",
        "property_name": "jurisdiction",
        "reason": "Frequent filtering",
        "requested_by": "analytics",
    }

    mock_saved = GraphIndexRequest(**request_data)

    with (
        patch("src.api.graph_routes.store_index_request") as mock_store,
        patch("src.api.graph_routes.get_db", return_value=iter([MagicMock()])),
    ):
        mock_store.return_value = mock_saved

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/graph/request-index", json=request_data)

        assert resp.status_code == 200
        data = resp.json()
        assert data["type_name"] == "Person"
        assert data["status"] == "pending"
