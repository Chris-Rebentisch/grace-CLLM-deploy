"""Tests for the one-time embedding backfill routine (CP4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.extraction.embedding_backfill import backfill_embeddings
from src.extraction.extraction_config import ExtractionSettings


@pytest.mark.asyncio
async def test_backfill_embeds_entities_lacking_vector():
    """Backfill embeds entities lacking a vector."""
    mock_client = AsyncMock()
    # First call: query for entities without embedding
    mock_client.execute_sql.side_effect = [
        {"result": [
            {"grace_id": "g1", "name": "Alpha Corp", "entity_type": "Legal_Entity"},
            {"grace_id": "g2", "name": "Beta Inc", "entity_type": "Legal_Entity"},
        ]},
        {"result": [{"count": 1}]},  # UPDATE for g1
        {"result": [{"count": 1}]},  # UPDATE for g2
    ]

    schema = {"entity_types": {"Legal_Entity": {"properties": {}}}}

    with patch("src.extraction.embedding_backfill.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]

        counts = await backfill_embeddings(mock_client, schema)

    assert counts.get("Legal_Entity") == 2
    assert mock_embed.call_count == 2
    # Verify UPDATE SQL was issued for each entity
    update_calls = [
        c for c in mock_client.execute_sql.call_args_list
        if "_embedding" in str(c) and "UPDATE" in str(c)
    ]
    assert len(update_calls) == 2


@pytest.mark.asyncio
async def test_backfill_idempotent_skips_already_embedded():
    """Idempotent re-run skips already-embedded entities."""
    mock_client = AsyncMock()
    # Query returns no entities (all already have embeddings)
    mock_client.execute_sql.return_value = {"result": []}

    schema = {"entity_types": {"Legal_Entity": {"properties": {}}}}

    with patch("src.extraction.embedding_backfill.embed_texts", new_callable=AsyncMock) as mock_embed:
        counts = await backfill_embeddings(mock_client, schema)

    assert counts == {}
    mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_backfilled_vectors_queryable():
    """Backfilled vectors are queryable via vectorNeighbors() (validated by UPDATE success)."""
    mock_client = AsyncMock()
    mock_client.execute_sql.side_effect = [
        {"result": [{"grace_id": "g1", "name": "Test", "entity_type": "TestType"}]},
        {"result": [{"count": 1}]},  # UPDATE success
    ]

    schema = {"entity_types": {"TestType": {"properties": {}}}}

    with patch("src.extraction.embedding_backfill.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.5] * 768]
        counts = await backfill_embeddings(mock_client, schema)

    assert counts.get("TestType") == 1
    # Verify the UPDATE SQL contains a vector literal
    update_call = mock_client.execute_sql.call_args_list[1]
    sql = str(update_call)
    assert "_embedding" in sql
    assert "0.5" in sql


def test_cli_entry_point_importable():
    """CLI entry point (__main__ block) is importable."""
    import importlib
    mod = importlib.import_module("src.extraction.embedding_backfill")
    assert hasattr(mod, "backfill_embeddings")
    assert hasattr(mod, "_main")


@pytest.mark.asyncio
async def test_re_embed_drops_is_null_clause_and_updates_all():
    """F-006 / ISS-0007: --re-embed regenerates vectors for ALL entities,
    including those already carrying an _embedding (space migration)."""
    mock_client = AsyncMock()
    mock_client.execute_sql.side_effect = [
        {"result": [
            {"grace_id": "g1", "name": "Alpha Corp", "entity_type": "Legal_Entity"},
        ]},
        {"result": [{"count": 1}]},  # UPDATE for g1
    ]
    schema = {"entity_types": {"Legal_Entity": {"properties": {}}}}

    with patch("src.extraction.embedding_backfill.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.1] * 768]
        counts = await backfill_embeddings(mock_client, schema, re_embed=True)

    assert counts.get("Legal_Entity") == 1
    select_sql = str(mock_client.execute_sql.call_args_list[0])
    assert "_embedding IS NULL" not in select_sql


@pytest.mark.asyncio
async def test_default_mode_keeps_is_null_clause():
    """Without --re-embed the WHERE clause still skips embedded entities."""
    mock_client = AsyncMock()
    mock_client.execute_sql.return_value = {"result": []}
    schema = {"entity_types": {"Legal_Entity": {"properties": {}}}}

    await backfill_embeddings(mock_client, schema)

    select_sql = str(mock_client.execute_sql.call_args_list[0])
    assert "_embedding IS NULL" in select_sql


@pytest.mark.asyncio
async def test_dry_run_counts_without_embedding_or_writing():
    """--dry-run reports candidates but never embeds or UPDATEs."""
    mock_client = AsyncMock()
    mock_client.execute_sql.return_value = {"result": [
        {"grace_id": "g1", "name": "Alpha Corp", "entity_type": "Legal_Entity"},
        {"grace_id": "g2", "name": "Beta Inc", "entity_type": "Legal_Entity"},
    ]}
    schema = {"entity_types": {"Legal_Entity": {"properties": {}}}}

    with patch("src.extraction.embedding_backfill.embed_texts", new_callable=AsyncMock) as mock_embed:
        counts = await backfill_embeddings(
            mock_client, schema, re_embed=True, dry_run=True
        )

    assert counts.get("Legal_Entity") == 2
    mock_embed.assert_not_called()
    assert mock_client.execute_sql.call_count == 1  # SELECT only, no UPDATEs
