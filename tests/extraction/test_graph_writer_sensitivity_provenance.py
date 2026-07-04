"""F-0047b / ISS-0055 Layers 1+2 — sensitivity-tag provenance at write time.

Pure unit tests (mocked ArcadeClient / Session — no services, no real DBs).

Layer 1: `_merge_sensitivity_provenance` semantics (tag->source map, per-write
counts, id cap + overflow, `_privileged_props` conservative maintenance) and
write_batch wiring on BOTH vertex tag-write paths (new-entity + ER-resolved
merge).

Layer 2: source sensitivity_tags threaded onto relationship/edge writes with
post-upsert most-restrictive-wins union.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.extraction.claim_models import Claim, ClaimStatus
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionBatch
from src.extraction.graph_writer import (
    _SENSITIVITY_SOURCE_ID_CAP,
    _merge_sensitivity_provenance,
    write_batch,
)
from src.graph.entity_models import EntityCreateResponse, RelationshipCreateResponse

# ---------------------------------------------------------------------------
# Layer 1 — pure merge-function semantics
# ---------------------------------------------------------------------------


def test_fresh_privileged_write_seeds_provenance():
    updates = _merge_sensitivity_provenance(
        {}, "doc-1", "|privileged|", ["name", "balance"]
    )
    sources = json.loads(updates["sensitivity_tag_sources"])
    assert sources["privileged"]["ids"] == ["doc-1"]
    assert sources["privileged"]["count"] == 1
    assert sources["privileged"]["overflow"] == 0
    assert updates["sensitivity_source_total"] == 1
    assert json.loads(updates["_privileged_props"]) == ["balance", "name"]


def test_clean_write_increments_total_only():
    updates = _merge_sensitivity_provenance({}, "doc-2", "", ["name"])
    assert updates["sensitivity_source_total"] == 1
    assert "sensitivity_tag_sources" not in updates
    assert "_privileged_props" not in updates


def test_merge_accumulates_counts_and_dedupes_ids():
    existing = {
        "sensitivity_tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-1"], "overflow": 0, "count": 1}}
        ),
        "sensitivity_source_total": 1,
        "_privileged_props": "[]",
    }
    # Same privileged doc re-extracted: count advances per WRITE, ids dedupe.
    updates = _merge_sensitivity_provenance(existing, "doc-1", "|privileged|", [])
    sources = json.loads(updates["sensitivity_tag_sources"])
    assert sources["privileged"]["ids"] == ["doc-1"]
    assert sources["privileged"]["count"] == 2
    assert updates["sensitivity_source_total"] == 2
    # Universal stays universal: tag count (2) == total (2) — re-extraction
    # of the same privileged doc cannot fake "clean evidence exists".


def test_id_cap_overflows_at_20():
    ids = [f"doc-{i}" for i in range(_SENSITIVITY_SOURCE_ID_CAP)]
    existing = {
        "sensitivity_tag_sources": json.dumps(
            {"privileged": {"ids": ids, "overflow": 0, "count": 20}}
        ),
        "sensitivity_source_total": 20,
    }
    updates = _merge_sensitivity_provenance(existing, "doc-new", "|privileged|", [])
    sources = json.loads(updates["sensitivity_tag_sources"])
    assert len(sources["privileged"]["ids"]) == _SENSITIVITY_SOURCE_ID_CAP
    assert "doc-new" not in sources["privileged"]["ids"]
    assert sources["privileged"]["overflow"] == 1
    assert sources["privileged"]["count"] == 21


def test_clean_source_deprivileges_explicitly_carried_props():
    existing = {
        "sensitivity_tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-1"], "overflow": 0, "count": 1}}
        ),
        "sensitivity_source_total": 1,
        "_privileged_props": json.dumps(["balance", "secret_note"]),
    }
    # Clean source explicitly carries "balance" (and not "secret_note").
    updates = _merge_sensitivity_provenance(existing, "doc-2", "", ["balance", "name"])
    assert json.loads(updates["_privileged_props"]) == ["secret_note"]
    assert updates["sensitivity_source_total"] == 2


def test_privileged_props_kept_unless_clean_source_supplies_them():
    """Conservative rule: a clean write NOT carrying the prop leaves it privileged."""
    existing = {
        "_privileged_props": json.dumps(["secret_note"]),
        "sensitivity_source_total": 1,
    }
    updates = _merge_sensitivity_provenance(existing, "doc-2", "", ["name"])
    # total moved but _privileged_props unchanged -> not in updates.
    assert "_privileged_props" not in updates
    assert updates["sensitivity_source_total"] == 2


def test_no_source_document_id_advances_nothing():
    updates = _merge_sensitivity_provenance({}, None, "", [])
    assert updates == {}


def test_corrupt_provenance_json_resets_safely():
    existing = {
        "sensitivity_tag_sources": "{not json",
        "sensitivity_source_total": "weird",
        "_privileged_props": "also not json",
    }
    updates = _merge_sensitivity_provenance(existing, "doc-1", "|privileged|", ["p"])
    sources = json.loads(updates["sensitivity_tag_sources"])
    assert sources["privileged"]["count"] == 1
    assert updates["sensitivity_source_total"] == 1
    assert json.loads(updates["_privileged_props"]) == ["p"]


# ---------------------------------------------------------------------------
# write_batch wiring — shared fixtures (mirrors test_graph_writer_evidence_origin)
# ---------------------------------------------------------------------------


SCHEMA = {
    "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
    "relationships": {},
}


def _mock_config() -> ExtractionSettings:
    return ExtractionSettings(
        extraction_base_url="http://localhost:11434",
        database_url="postgresql://localhost/test",
    )


def _entity_claim(**kw) -> Claim:
    base = dict(
        entity_type="Legal_Entity",
        subject_name="Acme Corp",
        subject_type="Legal_Entity",
        predicate="entity",
        properties_json={"name": "Acme Corp"},
        confidence=0.85,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        resolved_entity_grace_id=None,
    )
    base.update(kw)
    return Claim(**base)


def _rel_claim(**kw) -> Claim:
    base = dict(
        relationship_type="owns",
        subject_name="Acme Corp",
        subject_type="Legal_Entity",
        predicate="owns",
        object_name="Beta LLC",
        object_type="Legal_Entity",
        properties_json={},
        confidence=0.9,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
    )
    base.update(kw)
    return Claim(**base)


def _batch(claims: list[Claim]) -> ExtractionBatch:
    return ExtractionBatch(
        document_id="doc-1",
        claims=claims,
        entities=[],
        relationships=[],
        claims_accepted=len(claims),
        claims_quarantined=0,
    )


def _client() -> AsyncMock:
    client = AsyncMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})
    client.execute_sql = AsyncMock(return_value={"result": []})
    return client


def _write_patches(mock_insert=None, existing_entity=None):
    """Standard patch set for write_batch unit runs."""
    patches = [
        patch(
            "src.extraction.graph_writer.get_extraction_event",
            return_value={"status": "verified"},
        ),
        patch(
            "src.extraction.graph_writer.embed_texts",
            new_callable=AsyncMock,
            return_value=[[0.0] * 8],
        ),
        patch(
            "src.extraction.graph_writer.create_extraction_event_vertex",
            new_callable=AsyncMock,
            return_value="evt-gid",
        ),
        patch(
            "src.extraction.graph_writer.create_produced_by_edges",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch("src.extraction.graph_writer.update_event_status_after_write"),
    ]
    if mock_insert is not None:
        patches.append(
            patch(
                "src.extraction.graph_writer.insert_entity",
                new=mock_insert,
            )
        )
    if existing_entity is not None:
        patches.append(
            patch(
                "src.extraction.graph_writer.get_entity",
                new_callable=AsyncMock,
                return_value=existing_entity,
            )
        )
    return patches


def _provenance_set_calls(client) -> list[str]:
    return [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "sensitivity_tag_sources" in str(call.args[0])
    ]


# ---------------------------------------------------------------------------
# Layer 1 — new-entity path stamps provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_entity_privileged_write_stamps_provenance_and_priv_props():
    client = _client()
    mock_insert = AsyncMock(
        return_value=EntityCreateResponse(
            grace_id="new-gid",
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        )
    )
    patches = _write_patches(mock_insert=mock_insert)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        await write_batch(
            _batch([_entity_claim()]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="|privileged|",
        )
    sets = _provenance_set_calls(client)
    assert sets, "new-entity path must issue a provenance SET"
    assert "sensitivity_source_total = 1" in sets[0]
    assert "privileged" in sets[0]
    assert "_privileged_props" in sets[0]
    assert "name" in sets[0]  # domain prop stamped as privileged-contributed


@pytest.mark.asyncio
async def test_new_entity_clean_write_stamps_total_but_not_priv_props():
    client = _client()
    mock_insert = AsyncMock(
        return_value=EntityCreateResponse(
            grace_id="new-gid",
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        )
    )
    patches = _write_patches(mock_insert=mock_insert)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        await write_batch(
            _batch([_entity_claim()]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="",
        )
    total_sets = [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "sensitivity_source_total" in str(call.args[0])
    ]
    assert total_sets, "clean write must still increment sensitivity_source_total"
    assert "_privileged_props" not in total_sets[0]
    assert "sensitivity_tag_sources" not in total_sets[0]


# ---------------------------------------------------------------------------
# Layer 1 — ER-resolved merge path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_path_privileged_write_updates_provenance():
    client = _client()
    existing = {
        "name": "Acme Corp",
        "sensitivity_tags": "",
        "sensitivity_tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-0"], "overflow": 0, "count": 1}}
        ),
        "sensitivity_source_total": 3,
        "evidence_origin": "document",
    }
    patches = _write_patches(existing_entity=existing)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        await write_batch(
            _batch([_entity_claim(resolved_entity_grace_id="gid-1")]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="|privileged|",
        )
    sets = _provenance_set_calls(client)
    assert sets, "merge path must issue a provenance SET"
    assert "sensitivity_source_total = 4" in sets[0]
    assert "doc-1" in sets[0]  # new contributing source recorded
    # Merge path writes no domain props -> tagged source stamps NO props.
    assert "_privileged_props" not in sets[0]


@pytest.mark.asyncio
async def test_merge_path_clean_write_deprivileges_carried_props():
    client = _client()
    existing = {
        "name": "Acme Corp",
        "sensitivity_tags": "|privileged|",
        "sensitivity_tag_sources": json.dumps(
            {"privileged": {"ids": ["doc-0"], "overflow": 0, "count": 1}}
        ),
        "sensitivity_source_total": 1,
        "_privileged_props": json.dumps(["name"]),
        "evidence_origin": "document",
    }
    patches = _write_patches(existing_entity=existing)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        await write_batch(
            _batch([_entity_claim(resolved_entity_grace_id="gid-1")]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="",
        )
    priv_sets = [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "_privileged_props" in str(call.args[0])
    ]
    assert priv_sets, "clean source carrying 'name' must de-privilege it"
    assert "_privileged_props = '[]'" in priv_sets[0]


@pytest.mark.asyncio
async def test_sensitivity_tags_union_behavior_unchanged_on_merge():
    """Layer 1 invariant: the D520 union SET still fires exactly as before."""
    client = _client()
    existing = {
        "name": "Acme Corp",
        "sensitivity_tags": "|pii_dense|",
        "evidence_origin": "document",
    }
    patches = _write_patches(existing_entity=existing)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        await write_batch(
            _batch([_entity_claim(resolved_entity_grace_id="gid-1")]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="|privileged|",
        )
    union_sets = [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "SET n.sensitivity_tags" in str(call.args[0])
    ]
    assert union_sets
    assert "|pii_dense|privileged|" in union_sets[0]


# ---------------------------------------------------------------------------
# Layer 2 — edge sensitivity tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationship_write_carries_source_tags_and_unions():
    client = _client()
    mock_rel = AsyncMock(
        return_value=RelationshipCreateResponse(
            grace_id="edge-gid",
            relationship_type="owns",
            source_grace_id="gid-a",
            target_grace_id="gid-b",
        )
    )
    patches = _write_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
        "src.extraction.graph_writer.insert_relationship", new=mock_rel
    ), patch(
        "src.extraction.graph_writer.canonical_lookup",
        new_callable=AsyncMock,
        side_effect=["gid-a", "gid-b"],
    ), patch(
        "src.extraction.graph_writer.update_claim_resolved_endpoints"
    ):
        await write_batch(
            _batch([_rel_claim()]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="|privileged|",
        )
    assert mock_rel.await_count == 1
    rel_create = mock_rel.call_args[0][1]
    # Layer 2 — edge inherits the source's tags via the properties map.
    assert rel_create.properties.get("sensitivity_tags") == "|privileged|"
    # Post-upsert union SET issued (existing edge fetch returned no rows ->
    # union computes |privileged| vs "" -> SET fires).
    union_sets = [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "SET r.sensitivity_tags" in str(call.args[0])
    ]
    assert union_sets
    assert "|privileged|" in union_sets[0]


@pytest.mark.asyncio
async def test_relationship_write_clean_source_adds_no_edge_tags():
    client = _client()
    mock_rel = AsyncMock(
        return_value=RelationshipCreateResponse(
            grace_id="edge-gid",
            relationship_type="owns",
            source_grace_id="gid-a",
            target_grace_id="gid-b",
        )
    )
    patches = _write_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
        "src.extraction.graph_writer.insert_relationship", new=mock_rel
    ), patch(
        "src.extraction.graph_writer.canonical_lookup",
        new_callable=AsyncMock,
        side_effect=["gid-a", "gid-b"],
    ), patch(
        "src.extraction.graph_writer.update_claim_resolved_endpoints"
    ):
        await write_batch(
            _batch([_rel_claim()]),
            SCHEMA,
            client,
            MagicMock(),
            "evt-1",
            _mock_config(),
            sensitivity_tags="",
        )
    rel_create = mock_rel.call_args[0][1]
    assert "sensitivity_tags" not in rel_create.properties
    union_sets = [
        str(call.args[0])
        for call in client.execute_cypher.call_args_list
        if call.args and "SET r.sensitivity_tags" in str(call.args[0])
    ]
    assert union_sets == []
