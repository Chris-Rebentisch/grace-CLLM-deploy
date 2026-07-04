"""Tests for graph traversal strategy (mocked ArcadeDB)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.retrieval.graph_strategy import graph_search
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RetrievalQuery


def _mock_client() -> ArcadeClient:
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_graph_search_with_seed():
    """Graph search with seed entity returns connected nodes."""
    client = _mock_client()
    client.execute_cypher.return_value = {
        "result": [
            {"grace_id": "id-2", "@type": "Company", "name": "Acme", "_deprecated": False},
            {"grace_id": "id-3", "@type": "Person", "name": "Bob", "_deprecated": False},
        ]
    }
    query = RetrievalQuery(
        query_text="find related", seed_entity_ids=["id-1"]
    )
    config = RetrievalConfig()
    results = await graph_search(client, query, config)

    assert len(results) == 2
    assert results[0].grace_id == "id-2"
    assert results[0].strategy == "graph"
    # Verify variable-length path query was used
    call_args = client.execute_cypher.call_args[0][0]
    assert "[*1..3]" in call_args


@pytest.mark.asyncio
async def test_graph_search_with_temporal_filter():
    """Graph search with temporal filter adds WHERE clause."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    query = RetrievalQuery(
        query_text="find",
        seed_entity_ids=["id-1"],
        temporal_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        temporal_end=datetime(2023, 12, 31, tzinfo=timezone.utc),
    )
    config = RetrievalConfig(temporal_as_strategy=False)
    await graph_search(client, query, config)

    call_args = client.execute_cypher.call_args[0][0]
    assert "valid_from" in call_args
    assert "valid_to" in call_args


@pytest.mark.asyncio
async def test_graph_search_without_seeds():
    """Graph search without seeds falls back to name CONTAINS."""
    client = _mock_client()
    client.execute_cypher.return_value = {
        "result": [
            {"grace_id": "id-1", "@type": "Person", "name": "Alice Smith", "_deprecated": False},
        ]
    }
    query = RetrievalQuery(query_text="Alice")
    config = RetrievalConfig()
    results = await graph_search(client, query, config)

    assert len(results) == 1
    call_args = client.execute_cypher.call_args[0][0]
    assert "CONTAINS" in call_args
    assert "Alice" in call_args


@pytest.mark.asyncio
async def test_graph_search_respects_max_hop_depth():
    """Graph search respects max_hop_depth configuration."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    query = RetrievalQuery(query_text="test", seed_entity_ids=["id-1"])
    config = RetrievalConfig(max_hop_depth=5)
    await graph_search(client, query, config)

    call_args = client.execute_cypher.call_args[0][0]
    assert "[*1..5]" in call_args


@pytest.mark.asyncio
async def test_graph_search_deprecated_filter():
    """Graph search includes _deprecated = false in WHERE."""
    client = _mock_client()
    client.execute_cypher.return_value = {"result": []}
    query = RetrievalQuery(query_text="test", seed_entity_ids=["id-1"])
    config = RetrievalConfig()
    await graph_search(client, query, config)

    call_args = client.execute_cypher.call_args[0][0]
    assert "_deprecated = false" in call_args
