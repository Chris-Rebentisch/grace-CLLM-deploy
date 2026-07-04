"""Tests for Tier-2 ANN re-architecture (CP5).

Validates that _tier2_embedding() uses SQL vectorNeighbors() ANN queries
instead of the previous OpenCypher + BM25 + numpy path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.entity_resolver import EntityResolver, EntityResolutionResult
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity
from src.extraction.entity_registry import EntityRegistry


def _make_resolver(execute_sql_side_effect=None) -> tuple[EntityResolver, AsyncMock]:
    """Create a resolver with a mocked ArcadeDB client."""
    mock_client = AsyncMock()
    if execute_sql_side_effect:
        mock_client.execute_sql.side_effect = execute_sql_side_effect
    config = ExtractionSettings()
    resolver = EntityResolver(
        arcade_client=mock_client,
        config=config,
    )
    return resolver, mock_client


def _make_entity(name: str = "Test Corp", entity_type: str = "Legal_Entity") -> ExtractedEntity:
    return ExtractedEntity(
        name=name,
        entity_type=entity_type,
        properties={"name": name},
    )


@pytest.mark.asyncio
async def test_ann_query_issued_not_opencypher():
    """ANN query issued (not OpenCypher)."""
    resolver, mock_client = _make_resolver()
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Test Corp", "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]

        entity = _make_entity()
        registry = EntityRegistry()
        # Tier 1 miss
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    # Should have called execute_sql (not execute_cypher) for the ANN query
    sql_calls = mock_client.execute_sql.call_args_list
    assert any("vectorNeighbors" in str(c) for c in sql_calls)


@pytest.mark.asyncio
async def test_merge_threshold_gates_correctly():
    """Merge threshold gates correctly (score >= merge -> auto-merge)."""
    resolver, mock_client = _make_resolver()
    # distance 0.02 -> similarity 0.98, which exceeds Legal_Entity merge=0.90
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Match", "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    assert result.resolution_tier == "embedding"
    assert result.is_new is False
    assert result.resolved_grace_id == "g1"
    assert result.similarity_score >= 0.90


@pytest.mark.asyncio
async def test_review_threshold_gates_correctly():
    """Review threshold gates correctly (review <= score < merge -> tier3)."""
    resolver, mock_client = _make_resolver()
    # distance 0.15 -> similarity 0.85, between review=0.78 and merge=0.90
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Maybe", "_deprecated": False, "distance": 0.15},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    assert result.resolution_tier == "_tier3"  # sentinel for Tier 3


@pytest.mark.asyncio
async def test_tier3_escalation_on_low_scores():
    """Tier-3 escalation on low scores (below review threshold -> new entity)."""
    resolver, mock_client = _make_resolver()
    # distance 0.5 -> similarity 0.5, below review=0.78
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Unrelated", "_deprecated": False, "distance": 0.5},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    assert result.resolution_tier == "new"
    assert result.is_new is True


@pytest.mark.asyncio
async def test_d95_argmax_tiebreak_preserved():
    """D95 argmax/tiebreak preserved (lexicographic grace_id on tied scores)."""
    resolver, mock_client = _make_resolver()
    # Two candidates with identical distance (tied scores)
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g2", "name": "B Corp", "_deprecated": False, "distance": 0.02},
                {"grace_id": "g1", "name": "A Corp", "_deprecated": False, "distance": 0.02},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    # Lexicographic tiebreak: g1 < g2
    assert result.resolved_grace_id == "g1"


@pytest.mark.asyncio
async def test_deprecated_entities_excluded_from_candidates():
    """Deprecated entities excluded from candidate sets."""
    resolver, mock_client = _make_resolver()
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Active", "_deprecated": False, "distance": 0.02},
                {"grace_id": "g2", "name": "Deprecated", "_deprecated": True, "distance": 0.01},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    # Only g1 should be a candidate (g2 is deprecated)
    if result.candidates_json:
        candidate_ids = [c["grace_id"] for c in result.candidates_json]
        assert "g2" not in candidate_ids
        assert "g1" in candidate_ids


@pytest.mark.asyncio
async def test_candidates_json_shape_preserved():
    """candidates_json shape preserved."""
    resolver, mock_client = _make_resolver()
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Corp A", "_deprecated": False, "distance": 0.05},
                {"grace_id": "g2", "name": "Corp B", "_deprecated": False, "distance": 0.1},
            ]
        }]
    }

    with patch("src.extraction.entity_resolver.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        entity = _make_entity()
        registry = EntityRegistry()
        mock_client.execute_cypher.return_value = {"result": []}
        result = await resolver._tier2_embedding(entity, "type:Legal_Entity", registry)

    assert result is not None
    assert result.candidates_json is not None
    for c in result.candidates_json:
        assert "grace_id" in c
        assert "name" in c
        assert "score" in c
    # Should be sorted by score descending
    scores = [c["score"] for c in result.candidates_json]
    assert scores == sorted(scores, reverse=True)
