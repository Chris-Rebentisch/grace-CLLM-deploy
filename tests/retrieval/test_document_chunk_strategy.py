"""Tests for chunk-semantic retrieval strategy.

CP4 (D466): ANN query shape, result adapter, empty-result handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.retrieval.document_chunk_strategy import chunk_semantic_search


# ---------------------------------------------------------------------------
# Test 1: ANN query contains vectorNeighbors() and Document_Chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_semantic_ann_query_shape():
    """ANN query must contain vectorNeighbors() and Document_Chunk."""
    mock_client = AsyncMock()
    mock_client.execute_sql = AsyncMock(return_value={
        "result": [
            {
                "grace_id": "abc-123",
                "text": "Audit rights clause text",
                "source_document_id": "doc-1",
                "chunk_index": 3,
                "chunk_token_count": 50,
                "sensitivity_tags": "",
                "_deprecated": False,
                "score": 0.95,
            },
        ],
    })

    fake_embedding = [0.1] * 768

    with patch(
        "src.retrieval.document_chunk_strategy.embed_texts",
        new_callable=AsyncMock,
        return_value=[fake_embedding],
    ):
        results = await chunk_semantic_search(
            client=mock_client,
            query_text="what are the audit rights",
            top_k=10,
        )

    # Verify SQL query shape
    sql_call = mock_client.execute_sql.call_args[0][0]
    assert "vectorNeighbors" in sql_call
    assert "Document_Chunk" in sql_call

    assert len(results) == 1
    assert results[0].grace_id == "abc-123"
    assert results[0].strategy == "chunk_semantic"


# ---------------------------------------------------------------------------
# Test 2: Results adapt to RetrievalCandidate shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_result_adapter():
    """Results must adapt to RetrievalCandidate shape with correct fields."""
    mock_client = AsyncMock()
    mock_client.execute_sql = AsyncMock(return_value={
        "result": [
            {
                "grace_id": "chunk-1",
                "text": "Contract termination clause",
                "source_document_id": "doc-42",
                "chunk_index": 7,
                "chunk_token_count": 100,
                "sensitivity_tags": "|privileged|",
                "_deprecated": False,
                "score": 0.88,
            },
        ],
    })

    with patch(
        "src.retrieval.document_chunk_strategy.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.2] * 768],
    ):
        results = await chunk_semantic_search(
            client=mock_client,
            query_text="termination rights",
            top_k=5,
        )

    assert len(results) == 1
    r = results[0]
    assert r.entity_type == "Document_Chunk"
    assert r.strategy == "chunk_semantic"
    assert r.rank == 0
    assert r.properties["text"] == "Contract termination clause"
    assert r.properties["source_document_id"] == "doc-42"
    assert r.properties["chunk_index"] == 7
    assert r.properties["sensitivity_tags"] == "|privileged|"


# ---------------------------------------------------------------------------
# Test 3: Empty result handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_result_handling():
    """Empty ANN response returns empty list without error."""
    mock_client = AsyncMock()
    mock_client.execute_sql = AsyncMock(return_value={"result": []})

    with patch(
        "src.retrieval.document_chunk_strategy.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.3] * 768],
    ):
        results = await chunk_semantic_search(
            client=mock_client,
            query_text="nonexistent topic",
            top_k=10,
        )

    assert results == []
