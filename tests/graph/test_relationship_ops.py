"""Tests for relationship CRUD operations (mocked ArcadeDB, no live server)."""

from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.entity_models import RelationshipCreate
from src.graph.relationship_ops import get_relationship, insert_relationship


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mock_arcade_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_cypher."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    return client


# ===========================================================================
# insert_relationship tests
# ===========================================================================


@pytest.mark.asyncio
async def test_insert_relationship_success():
    """insert_relationship creates edge with grace_id and properties."""
    client = _mock_arcade_client()
    # F-012+F-018 / ISS-0009: insert now runs a duplicate-edge check first.
    client.execute_cypher.side_effect = [
        {"result": []},  # dup-check — no existing (source, type, target) edge
        {"result": [{"@rid": "#2:0", "@type": "owns", "grace_id": "edge-uuid"}]},  # CREATE
    ]
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="tgt-uuid",
        properties={"weight": 0.8},
    )
    result = await insert_relationship(client, rel)
    assert result.relationship_type == "owns"
    assert result.source_grace_id == "src-uuid"
    assert result.target_grace_id == "tgt-uuid"
    assert result.grace_id  # UUID was generated
    # Verify the CREATE Cypher query (second call, after the dup-check)
    query = client.execute_cypher.call_args_list[1][0][0]
    assert "MATCH" in query
    assert "CREATE" in query
    assert "owns" in query
    assert "src-uuid" in query
    assert "tgt-uuid" in query
    # Chunk 59 M7: evidence_origin is vertex-only — not on relationship edges.
    assert "evidence_origin" not in query


@pytest.mark.asyncio
async def test_insert_relationship_source_not_found():
    """insert_relationship raises ValueError when source vertex not found."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}  # empty = no match
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="missing-src",
        target_grace_id="tgt-uuid",
    )
    with pytest.raises(ValueError, match="Source.*or target.*not found"):
        await insert_relationship(client, rel)


@pytest.mark.asyncio
async def test_insert_relationship_target_not_found():
    """insert_relationship raises ValueError when target vertex not found."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="missing-tgt",
    )
    with pytest.raises(ValueError, match="not found"):
        await insert_relationship(client, rel)


# ===========================================================================
# get_relationship tests
# ===========================================================================


@pytest.mark.asyncio
async def test_get_relationship_found():
    """get_relationship returns dict when relationship exists."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {
        "result": [{"@rid": "#2:0", "grace_id": "edge-uuid", "weight": 0.8}]
    }
    result = await get_relationship(client, "edge-uuid")
    assert result is not None
    assert result["grace_id"] == "edge-uuid"
    assert result["weight"] == 0.8


@pytest.mark.asyncio
async def test_get_relationship_not_found():
    """get_relationship returns None when relationship does not exist."""
    client = _mock_arcade_client()
    client.execute_cypher.return_value = {"result": []}
    result = await get_relationship(client, "nonexistent")
    assert result is None
