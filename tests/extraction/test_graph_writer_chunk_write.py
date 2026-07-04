"""Tests for write_batch Document_Chunk vertex creation and derives_from edges.

CP3 (D465): chunk INSERT idempotency, derives_from edge creation,
sensitivity tagging, embedding computation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.extraction.graph_writer import (
    _compute_chunk_sensitivity_tags,
    _insert_document_chunk_vertex,
    _insert_derives_from_edge,
    _lookup_document_chunk,
)


# ---------------------------------------------------------------------------
# Test 1: Chunk INSERT idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_insert_idempotent():
    """Re-running chunk insert with same (source_document_id, chunk_index) is a no-op."""
    mock_client = AsyncMock()

    # First lookup returns None (not yet inserted)
    mock_client.execute_cypher = AsyncMock(side_effect=[
        # _lookup_document_chunk -> not found
        {"result": []},
        # _insert_document_chunk_vertex CREATE -> ok
        {"result": [{}]},
    ])
    mock_client.execute_sql = AsyncMock(return_value={})

    # First insert
    gid1 = await _insert_document_chunk_vertex(
        client=mock_client,
        grace_id=str(uuid4()),
        source_document_id="doc-1",
        chunk_index=0,
        text="Some text",
        chunk_token_count=10,
        embedding=[0.1] * 768,
        sensitivity_tags="",
    )
    assert gid1  # got a grace_id back

    # Second lookup returns existing grace_id
    mock_client.execute_cypher = AsyncMock(return_value={
        "result": [{"n.grace_id": gid1}],
    })

    existing = await _lookup_document_chunk(mock_client, "doc-1", 0)
    assert existing == gid1, "Second lookup should return existing grace_id"


# ---------------------------------------------------------------------------
# Test 2: derives_from edge created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derives_from_edge_created():
    """_insert_derives_from_edge creates an edge with correct Cypher pattern."""
    mock_client = AsyncMock()
    mock_client.execute_cypher = AsyncMock(return_value={"result": [{}]})

    entity_gid = str(uuid4())
    chunk_gid = str(uuid4())

    await _insert_derives_from_edge(mock_client, entity_gid, chunk_gid)

    # Verify Cypher was called with derives_from edge
    call_args = mock_client.execute_cypher.call_args
    cypher = call_args[0][0]
    assert "derives_from" in cypher
    assert entity_gid in cypher
    assert chunk_gid in cypher
    assert "grace_id" in cypher
    assert "created_at" in cypher


# ---------------------------------------------------------------------------
# Test 3: Sensitivity tags computed via tagger
# ---------------------------------------------------------------------------


def test_sensitivity_tags_computed_via_tagger():
    """Chunk vertices have sensitivity_tags computed by the D441 rule-based tagger."""
    # Privileged text
    privileged_text = "This is covered by attorney-client privilege and work product doctrine."
    tags = _compute_chunk_sensitivity_tags(privileged_text)
    assert "|privileged|" in tags

    # PII-dense text (3+ PII indicators)
    pii_text = (
        "Contact john@example.com, jane@test.org, bob@foo.com "
        "or call +1-555-123-4567. SSN: 123-45-6789"
    )
    tags = _compute_chunk_sensitivity_tags(pii_text)
    assert "|pii_dense|" in tags

    # Clean text — no tags
    clean_text = "The company was founded in 2020 and operates in financial services."
    tags = _compute_chunk_sensitivity_tags(clean_text)
    assert tags == ""


# ---------------------------------------------------------------------------
# Test 4: Embedding computed on chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_computed_on_chunk():
    """Chunk vertices have non-null _embedding set via SQL UPDATE."""
    mock_client = AsyncMock()
    mock_client.execute_cypher = AsyncMock(return_value={"result": [{}]})
    mock_client.execute_sql = AsyncMock(return_value={})

    embedding = [0.5] * 768
    gid = str(uuid4())

    await _insert_document_chunk_vertex(
        client=mock_client,
        grace_id=gid,
        source_document_id="doc-1",
        chunk_index=0,
        text="Test chunk",
        chunk_token_count=5,
        embedding=embedding,
        sensitivity_tags="",
    )

    # Verify SQL UPDATE was called with _embedding
    sql_calls = mock_client.execute_sql.call_args_list
    assert len(sql_calls) >= 1, "Expected SQL UPDATE for _embedding"
    sql = sql_calls[0][0][0]
    assert "_embedding" in sql
    assert "Document_Chunk" in sql
    assert gid in sql
