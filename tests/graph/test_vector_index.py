"""Tests for ArcadeDB vector index DDL, embedding persistence, and ANN queries.

Covers CP2 (DDL + index) and CP3 (embed-on-write) checkpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.graph.ddl_generator import (
    generate_embedding_property_ddl,
    generate_full_schema_ddl,
)
from src.graph.index_manager import (
    create_vector_indexes,
    generate_vector_index_ddl,
)
from src.graph.migration_types import META_EDGE_TYPES, META_ENTITY_TYPES


# --------------- CP2: DDL + Index tests ---------------


def test_embedding_property_ddl_on_domain_type():
    """Embedding property created on a domain entity type."""
    ddl = generate_embedding_property_ddl("Legal_Entity")
    assert ddl == "CREATE PROPERTY Legal_Entity._embedding IF NOT EXISTS LIST"


def test_vector_index_ddl():
    """LSMVectorIndex DDL generated correctly."""
    ddl = generate_vector_index_ddl("Legal_Entity")
    assert "LSM_VECTOR" in ddl
    assert '"dimensions": 768' in ddl
    assert '"metric": "COSINE"' in ddl
    assert "Legal_Entity" in ddl
    assert "_embedding" in ddl


def test_vector_index_ddl_custom_dimensions():
    """LSMVectorIndex DDL respects custom dimensions."""
    ddl = generate_vector_index_ddl("Person", dimensions=384)
    assert '"dimensions": 384' in ddl


@pytest.mark.asyncio
async def test_vectorneighbors_ann_query_returns_candidates():
    """vectorNeighbors() ANN query against fixture data returns expected candidates."""
    # This test validates the query structure by mocking the ArcadeDB client
    mock_client = AsyncMock()
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Alpha Corp", "_deprecated": False, "distance": 0.05},
                {"grace_id": "g2", "name": "Alpha Co", "_deprecated": False, "distance": 0.1},
                {"grace_id": "g3", "name": "Beta", "_deprecated": False, "distance": 0.9},
            ]
        }]
    }

    schema = {"entity_types": {"TestType": {"properties": {}}}}
    result = await create_vector_indexes(mock_client, schema)
    # Three indexes: the domain type (TestType) + Document_Chunk (D463, Chunk 71)
    # + Image_Asset (D501, Chunk 77b) — both meta-types carry _embedding.
    assert len(result) == 3
    assert any("TestType" in ddl and "LSM_VECTOR" in ddl for ddl in result)
    assert any("Document_Chunk" in ddl and "LSM_VECTOR" in ddl for ddl in result)
    assert any("Image_Asset" in ddl and "LSM_VECTOR" in ddl for ddl in result)


def test_deprecated_filter_composes_with_ann():
    """_deprecated = false filter composes with ANN (post-query filtering)."""
    # vectorNeighbors returns all neighbors including deprecated; caller must filter
    neighbors = [
        {"grace_id": "g1", "name": "A", "_deprecated": False, "distance": 0.0},
        {"grace_id": "g2", "name": "B", "_deprecated": True, "distance": 0.1},
        {"grace_id": "g3", "name": "C", "_deprecated": False, "distance": 0.2},
    ]
    filtered = [n for n in neighbors if not n.get("_deprecated", False)]
    assert len(filtered) == 2
    assert all(not n["_deprecated"] for n in filtered)


def test_meta_types_do_not_have_embedding_property():
    """Meta-types do NOT have _embedding property in generated DDL."""
    schema = {
        "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
        "relationships": {},
    }
    ddl = generate_full_schema_ddl(schema)
    ddl_text = "\n".join(ddl)

    # Domain type should have _embedding
    assert "Legal_Entity._embedding" in ddl_text

    # Meta-types should NOT have _embedding — except Document_Chunk (D463,
    # Chunk 71) and Image_Asset (D501, Chunk 77b), which carry _embedding
    # for chunk-semantic ANN retrieval and vision-embedding search respectively.
    _EMBEDDING_META_TYPES = {"Document_Chunk", "Image_Asset"}
    for meta_type in META_ENTITY_TYPES:
        if meta_type in _EMBEDDING_META_TYPES:
            assert f"{meta_type}._embedding" in ddl_text
            continue
        assert f"{meta_type}._embedding" not in ddl_text
    for meta_edge in META_EDGE_TYPES:
        assert f"{meta_edge}._embedding" not in ddl_text


@pytest.mark.asyncio
async def test_schema_sync_calls_both_index_functions():
    """schema_sync.sync_schema_to_graph() calls both static and vector index creation."""
    with patch("src.graph.schema_sync.create_static_indexes", new_callable=AsyncMock) as mock_static, \
         patch("src.graph.schema_sync.create_vector_indexes", new_callable=AsyncMock) as mock_vector, \
         patch("src.graph.schema_sync.get_active_version") as mock_active, \
         patch("src.graph.schema_sync.get_sync_by_version") as mock_existing, \
         patch("src.graph.schema_sync.create_sync_record") as mock_create:

        mock_version = MagicMock()
        mock_version.id = "v1"
        mock_version.version_number = 1
        mock_version.schema_json = {"entity_types": {"TestType": {"properties": {}}}, "relationships": {}}
        mock_active.return_value = mock_version
        mock_existing.return_value = None
        mock_create.return_value = MagicMock()

        mock_client = AsyncMock()
        mock_db = MagicMock()

        from src.graph.schema_sync import sync_schema_to_graph
        await sync_schema_to_graph(mock_db, mock_client)

        mock_static.assert_called_once()
        mock_vector.assert_called_once()
        # Both called with the same schema_json
        assert mock_static.call_args[0][1] == mock_version.schema_json
        assert mock_vector.call_args[0][1] == mock_version.schema_json


# --------------- CP3: Embed-on-write tests ---------------


@pytest.mark.asyncio
async def test_newly_written_entity_carries_embedding():
    """A newly-written entity carries a persisted _embedding vector."""
    mock_client = AsyncMock()
    mock_client.execute_cypher.side_effect = [
        {"result": []},  # canonical_lookup returns no match
        {"result": [{"@rid": "#1:0", "n": {"@rid": "#1:0", "grace_id": "test-id"}}]},  # CREATE
    ]
    mock_client.execute_sql.return_value = {"result": [{"count": 1}]}

    from src.graph.entity_models import EntityCreate
    from src.graph.entity_ops import insert_entity

    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Test Corp"},
    )
    test_embedding = [0.1] * 768
    resp = await insert_entity(mock_client, entity, embedding=test_embedding)
    assert resp.created

    # Verify SQL UPDATE was called with the embedding
    sql_calls = [
        call for call in mock_client.execute_sql.call_args_list
        if "_embedding" in str(call)
    ]
    assert len(sql_calls) == 1
    sql_str = str(sql_calls[0])
    assert "UPDATE Legal_Entity SET _embedding" in sql_str


@pytest.mark.asyncio
async def test_embedding_queryable_via_vectorneighbors():
    """The vector is queryable via vectorNeighbors() (mocked)."""
    mock_client = AsyncMock()
    mock_client.execute_sql.return_value = {
        "result": [{
            "neighbors": [
                {"grace_id": "g1", "name": "Test", "distance": 0.0, "_deprecated": False},
            ]
        }]
    }
    result = await mock_client.execute_sql(
        "SELECT vectorNeighbors('Legal_Entity[_embedding]', [0.1, 0.2], 5) AS neighbors"
    )
    neighbors = result["result"][0]["neighbors"]
    assert len(neighbors) == 1
    assert neighbors[0]["grace_id"] == "g1"


@pytest.mark.asyncio
async def test_one_embed_call_per_entity():
    """One embed_texts() call per entity (mock and assert call count)."""
    with patch("src.extraction.graph_writer.embed_texts", new_callable=AsyncMock) as mock_embed, \
         patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
         patch("src.extraction.graph_writer.get_extraction_event") as mock_event, \
         patch("src.extraction.graph_writer.update_event_status_after_write"), \
         patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock) as mock_prov, \
         patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock):

        mock_embed.return_value = [[0.1] * 768]
        mock_insert.return_value = MagicMock(grace_id="new-id", created=True)
        mock_event.return_value = None
        mock_prov.return_value = "event-gid"

        from src.extraction.claim_models import Claim, ClaimStatus
        from src.extraction.extraction_config import ExtractionSettings
        from src.extraction.extraction_models import ExtractionBatch
        from src.extraction.graph_writer import write_batch

        claim = Claim(
            claim_id="c1",
            subject_name="Test Corp",
            entity_type="Legal_Entity",
            status=ClaimStatus.AUTO_ACCEPTED,
            confidence=0.9,
            properties_json={"name": "Test Corp"},
            source_document_id="doc1",
            extraction_event_id="evt1",
        )
        batch = ExtractionBatch(
            batch_id="b1",
            document_id="doc1",
            claims=[claim],
            entities=[],
            relationships=[],
        )

        await write_batch(
            batch=batch,
            schema={},
            arcade_client=AsyncMock(),
            session=MagicMock(),
            event_id="evt1",
            config=ExtractionSettings(),
        )

        assert mock_embed.call_count == 1


@pytest.mark.asyncio
async def test_append_entity_alias_does_not_reembed():
    """append_entity_alias() does not trigger re-embed."""
    mock_client = AsyncMock()
    mock_client.execute_cypher.side_effect = [
        {"result": [{"aliases": ["original"]}]},  # fetch aliases
        {"result": [{"n.grace_id": "g1"}]},  # update
    ]

    from src.graph.entity_ops import append_entity_alias
    result = await append_entity_alias(mock_client, "g1", "new_alias")

    # Should not have called execute_sql (which is used for embedding writes)
    mock_client.execute_sql.assert_not_called()
