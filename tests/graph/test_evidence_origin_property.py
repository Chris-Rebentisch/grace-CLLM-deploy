"""Chunk 59, CP8 — evidence_origin vertex property tests.

Tests that:
1. EntityCreate defaults evidence_origin to 'document'.
2. EntityCreate accepts 'communication' and 'hybrid'.
3. insert_entity includes evidence_origin in the Cypher property map.
4. insert_relationship does NOT emit evidence_origin on edges (M7 vertex-only).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.graph.entity_models import EntityCreate, RelationshipCreate


# ---------------------------------------------------------------------------
# 1–3: Model defaults and acceptance.
# ---------------------------------------------------------------------------


def test_entity_create_evidence_origin_default() -> None:
    """EntityCreate defaults evidence_origin to 'document'."""
    e = EntityCreate(
        entity_type="Company",
        properties={"name": "Acme"},
    )
    assert e.evidence_origin == "document"


def test_entity_create_evidence_origin_communication() -> None:
    e = EntityCreate(
        entity_type="Person",
        properties={"name": "Alice"},
        evidence_origin="communication",
    )
    assert e.evidence_origin == "communication"


def test_entity_create_evidence_origin_hybrid() -> None:
    e = EntityCreate(
        entity_type="Person",
        properties={"name": "Bob"},
        evidence_origin="hybrid",
    )
    assert e.evidence_origin == "hybrid"


# ---------------------------------------------------------------------------
# 4: insert_entity includes evidence_origin in the Cypher property map.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_entity_cypher_includes_evidence_origin() -> None:
    """insert_entity should include evidence_origin in the CREATE Cypher."""
    from src.graph.entity_ops import insert_entity

    mock_client = AsyncMock()

    call_count = 0

    async def _side_effect(query, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # canonical_lookup → no match
            return {"result": []}
        # CREATE → return created node
        return {"result": [{"@rid": "#1:0", "grace_id": "new-gid"}]}

    mock_client.execute_cypher = AsyncMock(side_effect=_side_effect)

    entity = EntityCreate(
        entity_type="Company",
        properties={"name": "TestCorp"},
        evidence_origin="communication",
    )

    await insert_entity(mock_client, entity)

    calls = mock_client.execute_cypher.call_args_list
    assert len(calls) >= 2
    create_query = calls[1].args[0] if calls[1].args else calls[1].kwargs.get("query", "")
    assert "evidence_origin" in create_query
    assert "communication" in create_query


# ---------------------------------------------------------------------------
# 5: M7 — edges do not receive evidence_origin in Cypher.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_relationship_cypher_omits_evidence_origin_m7() -> None:
    """insert_relationship must not write evidence_origin on edges (spec M7)."""
    from src.graph.relationship_ops import insert_relationship

    mock_client = AsyncMock()
    # F-012+F-018 / ISS-0009: insert now runs a duplicate-edge check first,
    # so the CREATE is the second Cypher call.
    mock_client.execute_cypher = AsyncMock(
        side_effect=[
            {"result": []},  # dup-check — no existing edge
            {"result": [{"@rid": "#2:0", "grace_id": "rel-gid"}]},  # CREATE
        ]
    )

    rel = RelationshipCreate(
        relationship_type="mentions",
        source_grace_id="gid-1",
        target_grace_id="gid-2",
    )

    await insert_relationship(mock_client, rel)

    call_args = mock_client.execute_cypher.call_args_list
    assert len(call_args) >= 2
    create_query = call_args[1].args[0] if call_args[1].args else call_args[1].kwargs.get("query", "")
    assert "CREATE" in create_query
    assert "evidence_origin" not in create_query
