"""Tests for Document_Chunk META_ENTITY_TYPES and derives_from META_EDGE_TYPES.

CP1 (D463): META_ENTITY_TYPES 6→7, Document_Chunk 9 properties, vector index coverage.
CP2 (D464): META_EDGE_TYPES 4→5, derives_from 2 properties.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.migration_types import META_EDGE_TYPES, META_ENTITY_TYPES


# ---------------------------------------------------------------------------
# CP1 tests — Document_Chunk in META_ENTITY_TYPES
# ---------------------------------------------------------------------------


def test_meta_entity_types_count_7():
    """META_ENTITY_TYPES must have exactly 8 entries after D501."""
    assert len(META_ENTITY_TYPES) == 8, (
        f"Expected 8 META_ENTITY_TYPES, got {len(META_ENTITY_TYPES)}: "
        f"{list(META_ENTITY_TYPES.keys())}"
    )


def test_document_chunk_property_set():
    """Document_Chunk must have 9 properties including _embedding as LIST."""
    props = META_ENTITY_TYPES.get("Document_Chunk")
    assert props is not None, "Document_Chunk not found in META_ENTITY_TYPES"
    assert len(props) == 9, f"Expected 9 properties, got {len(props)}"

    prop_names = {p["name"] for p in props}
    expected = {
        "grace_id",
        "source_document_id",
        "chunk_index",
        "text",
        "chunk_token_count",
        "_embedding",
        "extracted_at",
        "sensitivity_tags",
        "_deprecated",
    }
    assert prop_names == expected, f"Property mismatch: {prop_names ^ expected}"

    # _embedding must be LIST type
    embedding_prop = next(p for p in props if p["name"] == "_embedding")
    assert embedding_prop["type"] == "LIST", (
        f"_embedding type must be LIST, got {embedding_prop['type']}"
    )


@pytest.mark.asyncio
async def test_create_vector_indexes_covers_meta_entity_types():
    """create_vector_indexes must generate vector index DDL for Document_Chunk."""
    from src.graph.index_manager import create_vector_indexes

    mock_client = AsyncMock()
    mock_client.execute_sql = AsyncMock(return_value={})

    # Empty schema_json — only meta-entity types should be covered
    executed = await create_vector_indexes(mock_client, {})

    # Should have at least one DDL for Document_Chunk
    doc_chunk_ddls = [d for d in executed if "Document_Chunk" in d]
    assert len(doc_chunk_ddls) >= 1, (
        f"Expected vector index DDL for Document_Chunk, got: {executed}"
    )
    assert "LSM_VECTOR" in doc_chunk_ddls[0]


# ---------------------------------------------------------------------------
# CP2 tests — derives_from in META_EDGE_TYPES
# ---------------------------------------------------------------------------


def test_meta_edge_types_count_5():
    """META_EDGE_TYPES must have exactly 5 entries after D464."""
    assert len(META_EDGE_TYPES) == 5, (
        f"Expected 5 META_EDGE_TYPES, got {len(META_EDGE_TYPES)}: "
        f"{list(META_EDGE_TYPES.keys())}"
    )


def test_derives_from_property_set():
    """derives_from must have 2 properties: grace_id and created_at."""
    props = META_EDGE_TYPES.get("derives_from")
    assert props is not None, "derives_from not found in META_EDGE_TYPES"
    assert len(props) == 2, f"Expected 2 properties, got {len(props)}"

    prop_names = {p["name"] for p in props}
    assert prop_names == {"grace_id", "created_at"}, (
        f"Property mismatch: {prop_names}"
    )
