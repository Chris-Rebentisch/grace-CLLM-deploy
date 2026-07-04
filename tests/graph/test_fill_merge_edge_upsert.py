"""Tests for fill-only property merge and edge upsert (mocked ArcadeDB).

Covers the validation-run data-integrity fixes:
- F-016 / ISS-0008: insert_entity canonical match must fill-only merge the
  incoming property payload (fill missing, never overwrite non-null, never
  write nulls) instead of dropping it (first-writer-wins).
- F-012+F-018 / ISS-0009: insert_relationship must upsert on
  (source vertex, edge type, target vertex) — no duplicate parallel edges;
  duplicate assertions fill-only merge properties and accumulate provenance.
"""

from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.entity_models import EntityCreate, RelationshipCreate
from src.graph.entity_ops import insert_entity
from src.graph.relationship_ops import insert_relationship


# ---------------------------------------------------------------------------
# Helper — build a mock ArcadeClient with mocked execute_cypher
# ---------------------------------------------------------------------------


def _mock_arcade_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked execute_cypher."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock()
    return client


# ===========================================================================
# F-016 / ISS-0008 — fill-only merge on canonical entity match
# ===========================================================================


@pytest.mark.asyncio
async def test_canonical_match_fills_missing_properties():
    """Fill-only merge SETs properties the existing vertex lacks.

    F-016 scenario: tax memo created the vertex with {name, ticker}; the
    portfolio statement's shares/market_value must land on canonical match.
    """
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"n.grace_id": "existing-uuid"}]},  # canonical_lookup hit
        # fetch existing — has name + ticker, lacks shares/market_value
        {"result": [{"@rid": "#1:5", "grace_id": "existing-uuid",
                     "name": "Helios Grid Energy", "ticker": "HLGE"}]},
        {"result": [{"n.grace_id": "existing-uuid"}]},  # fill-only merge SET
    ]
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={
            "name": "Helios Grid Energy",
            "ticker": "HLGE",
            "shares": 5200,
            "market_value": 366600.0,
        },
    )
    result = await insert_entity(client, entity)
    assert result.created is False
    assert result.canonical_match is True
    assert result.grace_id == "existing-uuid"
    # Third call is the fill-only merge SET
    assert client.execute_cypher.call_count == 3
    merge_query = client.execute_cypher.call_args_list[2][0][0]
    assert "SET" in merge_query
    assert "n.shares = 5200" in merge_query
    assert "n.market_value = 366600.0" in merge_query
    # Existing non-null values are never touched
    assert "n.name" not in merge_query
    assert "n.ticker" not in merge_query


@pytest.mark.asyncio
async def test_canonical_match_never_overwrites_non_null():
    """A conflicting incoming value for an existing non-null property is dropped."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"n.grace_id": "existing-uuid"}]},  # canonical_lookup hit
        {"result": [{"@rid": "#1:5", "grace_id": "existing-uuid",
                     "name": "Helios Grid Energy", "ticker": "HLGE"}]},  # fetch
        {"result": [{"n.grace_id": "existing-uuid"}]},  # fill-only merge SET
    ]
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={
            "name": "Helios Grid Energy",
            "ticker": "CONFLICT",  # existing has 'HLGE' — must NOT be overwritten
            "shares": 5200,
        },
    )
    await insert_entity(client, entity)
    merge_query = client.execute_cypher.call_args_list[2][0][0]
    assert "n.shares = 5200" in merge_query
    assert "ticker" not in merge_query
    assert "CONFLICT" not in merge_query


@pytest.mark.asyncio
async def test_canonical_match_never_writes_null():
    """Incoming null values are never written — and null-only payloads skip the SET."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"n.grace_id": "existing-uuid"}]},  # canonical_lookup hit
        {"result": [{"@rid": "#1:5", "grace_id": "existing-uuid",
                     "name": "Helios Grid Energy"}]},  # fetch
    ]
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Helios Grid Energy", "shares": None, "as_of": None},
    )
    result = await insert_entity(client, entity)
    assert result.canonical_match is True
    # Nothing fillable → NO merge SET issued (fetch is the last call)
    assert client.execute_cypher.call_count == 2


@pytest.mark.asyncio
async def test_canonical_match_fills_null_valued_existing_property():
    """A property present-but-null on the existing vertex counts as missing."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"n.grace_id": "existing-uuid"}]},  # canonical_lookup hit
        {"result": [{"@rid": "#1:5", "grace_id": "existing-uuid",
                     "name": "Helios Grid Energy", "shares": None}]},  # fetch
        {"result": [{"n.grace_id": "existing-uuid"}]},  # fill-only merge SET
    ]
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Helios Grid Energy", "shares": 5200},
    )
    await insert_entity(client, entity)
    merge_query = client.execute_cypher.call_args_list[2][0][0]
    assert "n.shares = 5200" in merge_query


# ===========================================================================
# F-012+F-018 / ISS-0009 — edge upsert on (source, type, target)
# ===========================================================================


@pytest.mark.asyncio
async def test_duplicate_edge_not_created_twice():
    """A second identical insert must not create a second physical edge."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        # --- first insert ---
        {"result": []},  # dup-check — no existing edge
        {"result": [{"@rid": "#2:0", "grace_id": "edge-1"}]},  # CREATE
        # --- second identical insert ---
        {"result": [{"@rid": "#2:0", "grace_id": "edge-1"}]},  # dup-check hit
    ]
    rel = RelationshipCreate(
        relationship_type="holds",
        source_grace_id="acct-uuid",
        target_grace_id="pos-uuid",
    )
    first = await insert_relationship(client, rel)
    second = await insert_relationship(client, rel)
    # No fillable props / no new provenance → no SET call either
    assert client.execute_cypher.call_count == 3
    create_queries = [
        c[0][0] for c in client.execute_cypher.call_args_list if "CREATE" in c[0][0]
    ]
    assert len(create_queries) == 1
    # Second insert returns the EXISTING edge's grace_id
    assert second.grace_id == "edge-1"
    assert second.relationship_type == "holds"
    assert first.grace_id != ""


@pytest.mark.asyncio
async def test_duplicate_edge_fill_merges_properties_and_accumulates_provenance():
    """Duplicate assertion fill-merges missing edge props + unions provenance."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        # dup-check — existing edge from doc-1 with share_pct already set
        {"result": [{"@rid": "#2:0", "grace_id": "edge-1",
                     "source_document_id": "doc-1", "share_pct": 40}]},
        {"result": [{"r.grace_id": "edge-1"}]},  # merge SET
    ]
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="tgt-uuid",
        properties={"share_pct": 55, "start_date": "2024-01-01", "notes": None},
        source_document_id="doc-2",
    )
    result = await insert_relationship(client, rel)
    assert result.grace_id == "edge-1"
    assert client.execute_cypher.call_count == 2
    merge_query = client.execute_cypher.call_args_list[1][0][0]
    assert "CREATE" not in merge_query
    # Fill-only: start_date fills; share_pct (non-null on existing) untouched;
    # null notes never written.
    assert "r.start_date = '2024-01-01'" in merge_query
    assert "share_pct" not in merge_query
    assert "notes" not in merge_query
    # Provenance accumulated, not dropped
    assert "r.source_document_ids = ['doc-1', 'doc-2']" in merge_query


@pytest.mark.asyncio
async def test_duplicate_edge_same_document_is_pure_noop():
    """Same-doc re-import of an identical edge issues no write at all."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"@rid": "#2:0", "grace_id": "edge-1",
                     "source_document_id": "doc-1"}]},  # dup-check hit
    ]
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="tgt-uuid",
        source_document_id="doc-1",  # same provenance — nothing to accumulate
    )
    result = await insert_relationship(client, rel)
    assert result.grace_id == "edge-1"
    assert client.execute_cypher.call_count == 1  # dup-check only


@pytest.mark.asyncio
async def test_duplicate_edge_extends_existing_provenance_list():
    """A prior source_document_ids list is extended, not replaced."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": [{"@rid": "#2:0", "grace_id": "edge-1",
                     "source_document_id": "doc-1",
                     "source_document_ids": ["doc-1", "doc-2"]}]},  # dup-check hit
        {"result": [{"r.grace_id": "edge-1"}]},  # merge SET
    ]
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="tgt-uuid",
        source_document_id="doc-3",
    )
    await insert_relationship(client, rel)
    merge_query = client.execute_cypher.call_args_list[1][0][0]
    assert "r.source_document_ids = ['doc-1', 'doc-2', 'doc-3']" in merge_query


@pytest.mark.asyncio
async def test_different_target_still_creates_new_edge():
    """Upsert key is (source, type, target) — a different target creates a new edge."""
    client = _mock_arcade_client()
    client.execute_cypher.side_effect = [
        {"result": []},  # dup-check — no edge to THIS target
        {"result": [{"@rid": "#2:1", "grace_id": "edge-2"}]},  # CREATE
    ]
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="other-tgt-uuid",
    )
    result = await insert_relationship(client, rel)
    assert result.grace_id  # fresh UUID minted
    create_query = client.execute_cypher.call_args_list[1][0][0]
    assert "CREATE" in create_query
    assert "other-tgt-uuid" in create_query
