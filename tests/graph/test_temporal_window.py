"""Tests for temporal windowed graph view (mocked ArcadeDB, no live server)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.management_models import TemporalWindowRequest
from src.graph.temporal_window import get_temporal_window


def _mock_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_cypher."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_window_filters_entities():
    """Temporal window returns entities within the time range."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        # Entity query
        {"result": [
            {"n": {"grace_id": "e1", "name": "Alice", "@type": "Person"}},
            {"n": {"grace_id": "e2", "name": "Bob", "@type": "Person"}},
        ]},
        # Relationship query
        {"result": [
            {"source": "e1", "target": "e2", "rel_type": "KNOWS", "r": {"grace_id": "r1"}},
        ]},
    ]
    request = TemporalWindowRequest(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 12, 31, tzinfo=UTC),
    )
    response = await get_temporal_window(client, request)
    assert response.entity_count == 2
    assert response.relationship_count == 1
    assert response.entities[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_null_valid_to_included():
    """Entities with null valid_to are treated as current (included in window)."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        {"result": [
            {"n": {"grace_id": "e1", "name": "Current", "valid_to": None}},
        ]},
        {"result": []},  # No relationships
    ]
    request = TemporalWindowRequest(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 12, 31, tzinfo=UTC),
    )
    response = await get_temporal_window(client, request)
    assert response.entity_count == 1
    # Verify the query includes IS NULL check for valid_to
    query = client.execute_cypher.call_args_list[0][0][0]
    assert "valid_to IS NULL" in query


@pytest.mark.asyncio
async def test_relationships_only_between_windowed_entities():
    """Relationships only returned between entities in the result set."""
    client = _mock_client()
    client.execute_cypher.side_effect = [
        {"result": [
            {"n": {"grace_id": "e1", "name": "Alice"}},
        ]},
        # Relationship query uses IN clause with entity IDs
        {"result": []},
    ]
    request = TemporalWindowRequest(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 6, 30, tzinfo=UTC),
    )
    response = await get_temporal_window(client, request)
    assert response.entity_count == 1
    assert response.relationship_count == 0
    # Verify the relationship query filters by entity IDs
    rel_query = client.execute_cypher.call_args_list[1][0][0]
    assert "e1" in rel_query


@pytest.mark.asyncio
async def test_empty_window():
    """Empty time window returns empty lists."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    request = TemporalWindowRequest(
        start=datetime(2099, 1, 1, tzinfo=UTC),
        end=datetime(2099, 12, 31, tzinfo=UTC),
        include_relationships=False,
    )
    response = await get_temporal_window(client, request)
    assert response.entity_count == 0
    assert response.relationship_count == 0
    assert response.entities == []
    assert response.relationships == []
