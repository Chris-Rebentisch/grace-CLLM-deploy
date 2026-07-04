"""Tests for vectorNeighbors()-based fuzzy duplicate detection (CP6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.dedup_detection import detect_fuzzy_duplicates


@pytest.mark.asyncio
async def test_ann_path_produces_duplicate_candidates():
    """ANN path produces equivalent DuplicateCandidate set on fixture data."""
    mock_client = AsyncMock()
    # _get_vertex_types
    mock_client.execute_sql.side_effect = [
        # _get_vertex_types
        {"result": [{"type_name": "TestType"}]},
        # _get_entities_for_type (via execute_cypher)
    ]
    mock_client.execute_cypher.return_value = {
        "result": [
            {"n.grace_id": "g1", "n.name": "Alpha Corp"},
            {"n.grace_id": "g2", "n.name": "Alpha Company"},
        ]
    }

    # After initial setup calls, mock the per-entity ANN queries
    embed_calls = [
        # g1 embedding fetch
        {"result": [{"_embedding": [1.0, 0.0, 0.0]}]},
        # g1 ANN query
        {"result": [{"neighbors": [
            {"grace_id": "g1", "name": "Alpha Corp", "_deprecated": False, "distance": 0.0},
            {"grace_id": "g2", "name": "Alpha Company", "_deprecated": False, "distance": 0.05},
        ]}]},
        # g2 embedding fetch
        {"result": [{"_embedding": [0.95, 0.05, 0.0]}]},
        # g2 ANN query
        {"result": [{"neighbors": [
            {"grace_id": "g1", "name": "Alpha Corp", "_deprecated": False, "distance": 0.05},
            {"grace_id": "g2", "name": "Alpha Company", "_deprecated": False, "distance": 0.0},
        ]}]},
    ]

    call_count = [0]
    original_side_effect = mock_client.execute_sql.side_effect

    async def sql_side_effect(sql, *args, **kwargs):
        if "vectorNeighbors" in sql or "SELECT _embedding" in sql:
            idx = call_count[0]
            call_count[0] += 1
            return embed_calls[idx]
        elif "DISTINCT @type" in sql:
            return {"result": [{"type_name": "TestType"}]}
        return {"result": []}

    mock_client.execute_sql.side_effect = sql_side_effect

    report = await detect_fuzzy_duplicates(
        mock_client,
        entity_type="TestType",
        similarity_threshold=0.85,
    )

    assert report.total_candidates >= 1
    for c in report.candidates:
        assert c.match_type == "embedding_similarity"
        assert c.similarity_score is not None
        assert c.similarity_score >= 0.85


@pytest.mark.asyncio
async def test_grace_id_ordered_pair_dedup():
    """grace_id-ordered pair dedup preserved (a < b)."""
    mock_client = AsyncMock()
    mock_client.execute_cypher.return_value = {
        "result": [
            {"n.grace_id": "g2", "n.name": "B"},
            {"n.grace_id": "g1", "n.name": "A"},
        ]
    }

    # Mock the per-entity queries
    async def sql_side_effect(sql, *args, **kwargs):
        if "SELECT _embedding" in sql:
            return {"result": [{"_embedding": [1.0, 0.0]}]}
        elif "vectorNeighbors" in sql:
            return {"result": [{"neighbors": [
                {"grace_id": "g1", "name": "A", "_deprecated": False, "distance": 0.05},
                {"grace_id": "g2", "name": "B", "_deprecated": False, "distance": 0.05},
            ]}]}
        return {"result": []}

    mock_client.execute_sql.side_effect = sql_side_effect

    report = await detect_fuzzy_duplicates(
        mock_client,
        entity_type="TestType",
        similarity_threshold=0.85,
    )

    for c in report.candidates:
        assert c.entity_a_grace_id < c.entity_b_grace_id


@pytest.mark.asyncio
async def test_deprecated_entities_excluded():
    """Deprecated entities excluded from fuzzy dedup."""
    mock_client = AsyncMock()
    mock_client.execute_cypher.return_value = {
        "result": [
            {"n.grace_id": "g1", "n.name": "Active"},
            {"n.grace_id": "g2", "n.name": "Also Active"},
        ]
    }

    async def sql_side_effect(sql, *args, **kwargs):
        if "SELECT _embedding" in sql:
            return {"result": [{"_embedding": [1.0, 0.0]}]}
        elif "vectorNeighbors" in sql:
            return {"result": [{"neighbors": [
                {"grace_id": "g1", "name": "Active", "_deprecated": False, "distance": 0.05},
                {"grace_id": "dep1", "name": "Deprecated", "_deprecated": True, "distance": 0.01},
            ]}]}
        return {"result": []}

    mock_client.execute_sql.side_effect = sql_side_effect

    report = await detect_fuzzy_duplicates(
        mock_client,
        entity_type="TestType",
        similarity_threshold=0.85,
    )

    for c in report.candidates:
        assert "dep1" not in (c.entity_a_grace_id, c.entity_b_grace_id)


def test_import_source_is_shared_embeddings():
    """Import source is src.shared.embeddings (not src.retrieval.semantic_strategy)."""
    import inspect
    import src.graph.dedup_detection as mod
    source = inspect.getsource(mod)
    assert "from src.shared.embeddings import" in source
    assert "from src.retrieval.semantic_strategy import embed_texts" not in source
