"""Tests for graph schema sync API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.graph.schema_sync_models import GraphSchemaSyncRecord


def _mock_db():
    """Create a mock DB session for dependency injection."""
    return MagicMock()


@pytest.mark.asyncio
async def test_sync_schema_endpoint():
    """POST /api/graph/sync-schema returns sync record."""
    record = GraphSchemaSyncRecord(
        ontology_version_id=str(uuid4()),
        ontology_version_number=3,
        status="success",
        total_statements=10,
        succeeded=10,
        failed=0,
        completed_at=datetime.now(UTC),
    )

    with (
        patch("src.api.graph_routes.sync_schema_to_graph", new_callable=AsyncMock) as mock_sync,
        patch("src.api.graph_routes.get_db", return_value=iter([_mock_db()])),
    ):
        mock_sync.return_value = record

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/graph/sync-schema")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["ontology_version_number"] == 3
        assert data["succeeded"] == 10


@pytest.mark.asyncio
async def test_schema_status_endpoint():
    """GET /api/graph/schema-status returns sync state."""
    status_data = {
        "ontology_version": 3,
        "graph_version": 3,
        "in_sync": True,
        "last_sync_at": datetime.now(UTC).isoformat(),
        "last_sync_status": "success",
    }

    with (
        patch("src.api.graph_routes.get_sync_status", new_callable=AsyncMock) as mock_status,
        patch("src.api.graph_routes.get_db", return_value=iter([_mock_db()])),
    ):
        mock_status.return_value = status_data

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/schema-status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["in_sync"] is True
        assert data["ontology_version"] == 3


@pytest.mark.asyncio
async def test_preview_sync_endpoint():
    """POST /api/graph/preview-sync returns DDL without executing."""
    preview_data = {
        "version_number": 3,
        "ddl_statements": [
            "CREATE VERTEX TYPE Person IF NOT EXISTS",
            "CREATE PROPERTY Person.name IF NOT EXISTS STRING",
        ],
        "statement_count": 2,
    }

    with (
        patch("src.api.graph_routes.preview_sync", new_callable=AsyncMock) as mock_preview,
        patch("src.api.graph_routes.get_db", return_value=iter([_mock_db()])),
    ):
        mock_preview.return_value = preview_data

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/graph/preview-sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["version_number"] == 3
        assert data["statement_count"] == 2
        assert len(data["ddl_statements"]) == 2


@pytest.mark.asyncio
async def test_sync_schema_arcade_unavailable():
    """POST /api/graph/sync-schema returns 503 when ArcadeDB is down."""
    with (
        patch("src.api.graph_routes.sync_schema_to_graph", new_callable=AsyncMock) as mock_sync,
        patch("src.api.graph_routes.get_db", return_value=iter([_mock_db()])),
    ):
        mock_sync.side_effect = ConnectionError("ArcadeDB not reachable")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/graph/sync-schema")

        assert resp.status_code == 503
