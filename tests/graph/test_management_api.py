"""Tests for graph management API endpoints (mocked dependencies)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.graph.management_models import (
    DuplicateReport,
    GraphHealthReport,
    GraphNamespace,
    OrphanReport,
    TemporalWindowResponse,
    TypeCount,
)

client = TestClient(app)


@pytest.fixture
def mock_arcade_client():
    """Mock the _get_client function to avoid real ArcadeDB connections."""
    with patch("src.api.management_routes._get_client") as mock:
        mock.return_value = AsyncMock()
        yield mock.return_value


@pytest.fixture
def mock_db():
    """Mock the get_db dependency."""
    from unittest.mock import MagicMock
    mock_session = MagicMock()

    def override_get_db():
        yield mock_session

    app.dependency_overrides[__import__("src.shared.database", fromlist=["get_db"]).get_db] = override_get_db
    yield mock_session
    app.dependency_overrides.clear()


@patch("src.api.management_routes.get_health_report")
@patch("src.api.management_routes._get_client")
def test_get_health(mock_client, mock_health):
    """GET /management/health returns 200 with GraphHealthReport."""
    mock_health.return_value = GraphHealthReport(
        total_vertices=100,
        total_edges=200,
        density=2.0,
        orphan_count=5,
        orphan_rate=0.05,
        avg_edges_per_vertex=2.0,
        vertex_types=[TypeCount(type_name="Person", count=100)],
        edge_types=[TypeCount(type_name="KNOWS", count=200)],
        deprecated_vertices=0,
        deprecated_edges=0,
    )
    resp = client.get("/api/graph/management/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_vertices"] == 100
    assert data["total_edges"] == 200


@patch("src.api.management_routes.detect_orphans")
@patch("src.api.management_routes._get_client")
def test_get_orphans(mock_client, mock_orphans):
    """GET /management/orphans returns 200 with OrphanReport."""
    mock_orphans.return_value = OrphanReport(
        orphan_count=2,
        total_entities=50,
        orphan_rate=0.04,
        orphans=[],
    )
    resp = client.get("/api/graph/management/orphans")
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_count"] == 2


@patch("src.api.management_routes.get_temporal_window")
@patch("src.api.management_routes._get_client")
def test_post_temporal_window(mock_client, mock_window):
    """POST /management/temporal-window returns 200."""
    mock_window.return_value = TemporalWindowResponse(
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 12, 31, tzinfo=UTC),
        entities=[{"grace_id": "e1", "name": "Alice"}],
        relationships=[],
        entity_count=1,
        relationship_count=0,
    )
    resp = client.post(
        "/api/graph/management/temporal-window",
        json={
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-12-31T00:00:00Z",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_count"] == 1


@patch("src.api.management_routes.list_namespaces")
def test_get_namespaces(mock_list, mock_db):
    """GET /management/namespaces returns 200."""
    mock_list.return_value = [
        GraphNamespace(database_name="child_1"),
    ]
    resp = client.get("/api/graph/management/namespaces")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["database_name"] == "child_1"


@patch("src.api.management_routes.register_namespace")
@patch("src.api.management_routes._get_client")
def test_post_namespace(mock_client, mock_register, mock_db):
    """POST /management/namespaces returns 200."""
    ns = GraphNamespace(database_name="new_graph")
    mock_register.return_value = ns
    resp = client.post(
        "/api/graph/management/namespaces",
        json={"database_name": "new_graph"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["database_name"] == "new_graph"


@patch("src.api.management_routes.detect_duplicates")
@patch("src.api.management_routes._get_client")
def test_get_duplicates(mock_client, mock_dedup):
    """GET /management/duplicates returns 200 with DuplicateReport."""
    mock_dedup.return_value = DuplicateReport(
        total_candidates=0,
        by_type={},
        candidates=[],
    )
    resp = client.get("/api/graph/management/duplicates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 0
