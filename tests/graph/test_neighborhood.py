"""Tests for graph neighborhood queries."""

from unittest.mock import AsyncMock

import pytest

from src.graph.neighborhood import fetch_entity_neighborhood


def _make_client(cypher_responses: dict | None = None):
    """Create a mock ArcadeClient with configurable cypher responses."""
    client = AsyncMock()
    if cypher_responses is None:
        cypher_responses = {}

    call_count = {"n": 0}
    responses = list(cypher_responses.values()) if cypher_responses else []

    async def mock_execute_cypher(query, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(responses):
            return responses[idx]
        return {"result": []}

    client.execute_cypher = AsyncMock(side_effect=mock_execute_cypher)
    return client


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_returns_structure():
    """fetch_entity_neighborhood returns dict with seed/neighbors/edges keys."""
    seed_entity = {"grace_id": "seed-1", "name": "Acme", "@type": "Legal_Entity"}
    client = _make_client()
    # Seed query returns entity
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"seed": seed_entity}]},  # seed fetch
        {"result": [  # outgoing
            {
                "source_grace_id": "seed-1",
                "relationship_type": "owns",
                "target_grace_id": "n-1",
                "neighbor": {"grace_id": "n-1", "name": "SubCo", "@type": "Legal_Entity"},
                "edge": {"confidence": 0.9},
            }
        ]},
        {"result": []},  # incoming
    ])

    result = await fetch_entity_neighborhood(client, "seed-1", max_depth=1)

    assert "seed" in result
    assert "neighbors" in result
    assert "edges" in result
    assert result["seed"]["grace_id"] == "seed-1"
    assert len(result["neighbors"]) == 1
    assert result["neighbors"][0]["name"] == "SubCo"
    assert len(result["edges"]) == 1
    assert result["edges"][0]["relationship_type"] == "owns"


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_empty_graph():
    """No results returns empty neighbors/edges but seed if found."""
    seed_entity = {"grace_id": "seed-1", "name": "Lonely"}
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"seed": seed_entity}]},  # seed
        {"result": []},  # outgoing
        {"result": []},  # incoming
    ])

    result = await fetch_entity_neighborhood(client, "seed-1", max_depth=1)

    assert result["seed"]["name"] == "Lonely"
    assert result["neighbors"] == []
    assert result["edges"] == []


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_includes_incoming():
    """Both outgoing and incoming edges are captured."""
    seed_entity = {"grace_id": "seed-1", "name": "Center"}
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"seed": seed_entity}]},  # seed
        {"result": [  # outgoing
            {
                "source_grace_id": "seed-1",
                "relationship_type": "owns",
                "target_grace_id": "out-1",
                "neighbor": {"grace_id": "out-1", "name": "OutNode"},
                "edge": {},
            }
        ]},
        {"result": [  # incoming
            {
                "source_grace_id": "in-1",
                "relationship_type": "manages",
                "target_grace_id": "seed-1",
                "neighbor": {"grace_id": "in-1", "name": "InNode"},
                "edge": {},
            }
        ]},
    ])

    result = await fetch_entity_neighborhood(client, "seed-1", max_depth=1)

    assert len(result["neighbors"]) == 2
    names = {n["name"] for n in result["neighbors"]}
    assert "OutNode" in names
    assert "InNode" in names
    assert len(result["edges"]) == 2


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_respects_max_depth():
    """max_depth=1 does not trigger depth-2 expansion."""
    seed_entity = {"grace_id": "seed-1", "name": "Root"}
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"seed": seed_entity}]},
        {"result": []},  # outgoing
        {"result": []},  # incoming
    ])

    result = await fetch_entity_neighborhood(client, "seed-1", max_depth=1)

    # With max_depth=1, only 3 calls: seed + out + in
    assert client.execute_cypher.call_count == 3


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_excludes_deprecated():
    """WHERE clause includes _deprecated = false."""
    seed_entity = {"grace_id": "seed-1", "name": "Root"}
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"seed": seed_entity}]},
        {"result": []},
        {"result": []},
    ])

    await fetch_entity_neighborhood(client, "seed-1", max_depth=1)

    # Check that outgoing/incoming queries include _deprecated filter
    calls = client.execute_cypher.call_args_list
    for call in calls[1:]:
        query = call.args[0] if call.args else call.kwargs.get("query", "")
        assert "_deprecated = false" in query


@pytest.mark.asyncio
async def test_fetch_entity_neighborhood_seed_not_found():
    """Returns empty result when seed entity doesn't exist."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})

    result = await fetch_entity_neighborhood(client, "nonexistent", max_depth=1)

    assert result["seed"] == {}
    assert result["neighbors"] == []
    assert result["edges"] == []
