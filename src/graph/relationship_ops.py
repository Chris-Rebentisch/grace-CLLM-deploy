"""Relationship CRUD operations against ArcadeDB via OpenCypher.

All functions are async. All take an ArcadeClient instance.
All queries use language="opencypher" (never "cypher").
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import build_property_map, build_set_clause, escape_cypher_string
from src.graph.entity_models import RelationshipCreate, RelationshipCreateResponse

logger = structlog.get_logger()


def _extract_edge(result_item: dict, alias: str = "r") -> dict:
    """Extract an edge from an ArcadeDB result item."""
    if alias in result_item and isinstance(result_item[alias], dict):
        return result_item[alias]
    return result_item


async def _upsert_existing_edge(
    client: ArcadeClient,
    rel: RelationshipCreate,
    existing: dict,
) -> RelationshipCreateResponse:
    """F-012+F-018 / ISS-0009: fill-only merge onto an existing parallel edge.

    Called when an edge of the same type already exists between the same two
    vertices. Fill-only policy mirrors F-016 entity merge: SET properties the
    existing edge lacks (absent or null), NEVER overwrite non-null, never
    write nulls. Provenance is accumulated (not dropped): the new assertion's
    source_document_id is unioned into a `source_document_ids` list property.
    """
    src_escaped = escape_cypher_string(rel.source_grace_id)
    tgt_escaped = escape_cypher_string(rel.target_grace_id)

    fill_props = {
        k: v
        for k, v in rel.properties.items()
        if v is not None and existing.get(k) is None
    }
    set_parts: list[str] = []
    set_clause = build_set_clause("r", fill_props)
    if set_clause:
        set_parts.append(set_clause.removeprefix("SET "))

    # Provenance accumulation: union existing source_document_id(s) with the
    # new assertion's, so a corroborating document is recorded rather than
    # silently dropped (the old path dropped the whole duplicate assertion).
    doc_ids: list[str] = []
    prior_list = existing.get("source_document_ids")
    if isinstance(prior_list, list):
        doc_ids.extend(str(d) for d in prior_list)
    prior_single = existing.get("source_document_id")
    if prior_single and str(prior_single) not in doc_ids:
        doc_ids.append(str(prior_single))
    if rel.source_document_id and str(rel.source_document_id) not in doc_ids:
        doc_ids.append(str(rel.source_document_id))
        # List literal built manually — format_cypher_value cannot serialize
        # lists (same constraint as the aliases/_embedding paths).
        ids_literal = "[" + ", ".join(
            f"'{escape_cypher_string(d)}'" for d in doc_ids
        ) + "]"
        set_parts.append(f"r.source_document_ids = {ids_literal}")

    if set_parts:
        query = (
            f"MATCH (a {{grace_id: '{src_escaped}'}})"
            f"-[r:{rel.relationship_type}]->"
            f"(b {{grace_id: '{tgt_escaped}'}}) "
            f"SET {', '.join(set_parts)} RETURN r.grace_id LIMIT 1"
        )
        await client.execute_cypher(query)

    existing_grace_id = existing.get("grace_id", "")
    logger.info(
        "relationship.duplicate_merged",
        relationship_type=rel.relationship_type,
        grace_id=existing_grace_id,
        source=rel.source_grace_id,
        target=rel.target_grace_id,
        filled=sorted(fill_props),
    )
    return RelationshipCreateResponse(
        grace_id=existing_grace_id,
        relationship_type=rel.relationship_type,
        source_grace_id=rel.source_grace_id,
        target_grace_id=rel.target_grace_id,
    )


async def insert_relationship(
    client: ArcadeClient,
    rel: RelationshipCreate,
    superseded_by: str | None = None,
) -> RelationshipCreateResponse:
    """Insert a single relationship (edge) between two entities by grace_id.

    Upserts on (source vertex, edge type, target vertex): if an edge of the
    same type already exists between the same two vertices, no second physical
    edge is created — its properties are fill-only merged instead (F-012+F-018
    / ISS-0009: duplicate parallel edges made count/sum aggregates wrong,
    e.g. count(p)=7 vs count(DISTINCT p.grace_id)=6).

    Raises ValueError if source or target vertex not found.
    """
    # F-012+F-018 / ISS-0009: edge upsert keyed on (source, type, target) —
    # check for an existing edge before CREATE.
    dup_query = (
        f"MATCH (a {{grace_id: '{escape_cypher_string(rel.source_grace_id)}'}})"
        f"-[r:{rel.relationship_type}]->"
        f"(b {{grace_id: '{escape_cypher_string(rel.target_grace_id)}'}}) "
        f"RETURN r LIMIT 1"
    )
    dup_result = await client.execute_cypher(dup_query)
    dup_rows = dup_result.get("result", [])
    if dup_rows:
        return await _upsert_existing_edge(client, rel, _extract_edge(dup_rows[0]))

    grace_id = str(uuid4())
    now = datetime.now(UTC)

    all_props: dict = {"grace_id": grace_id}
    all_props.update(rel.properties)
    all_props.update({
        "valid_from": rel.valid_from,
        "valid_to": rel.valid_to,
        "extracted_at": now,
        "extraction_confidence": rel.extraction_confidence,
        "relationship_confidence": rel.relationship_confidence,
        "source_document_id": rel.source_document_id,
        "extraction_event_id": rel.extraction_event_id,
        "schema_version": rel.schema_version,
        "ontology_module": rel.ontology_module,
        "human_validated": False,
        # Chunk 59 M7: evidence_origin is vertex-only; edges do not carry it.
        # D514 — additive `superseded_by` kwarg on edge write; mirrors vertex pattern.
        "superseded_by": superseded_by,
        "_deprecated": False,
    })

    prop_map = build_property_map(all_props)
    src_escaped = escape_cypher_string(rel.source_grace_id)
    tgt_escaped = escape_cypher_string(rel.target_grace_id)

    query = (
        f"MATCH (a {{grace_id: '{src_escaped}'}}), (b {{grace_id: '{tgt_escaped}'}}) "
        f"CREATE (a)-[r:{rel.relationship_type} {prop_map}]->(b) RETURN r"
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])

    if not rows:
        raise ValueError(
            f"Source ({rel.source_grace_id}) or target ({rel.target_grace_id}) "
            f"vertex not found for relationship {rel.relationship_type}"
        )

    logger.info(
        "relationship.created",
        relationship_type=rel.relationship_type,
        grace_id=grace_id,
        source=rel.source_grace_id,
        target=rel.target_grace_id,
    )
    return RelationshipCreateResponse(
        grace_id=grace_id,
        relationship_type=rel.relationship_type,
        source_grace_id=rel.source_grace_id,
        target_grace_id=rel.target_grace_id,
    )


async def get_relationship(client: ArcadeClient, grace_id: str) -> dict | None:
    """Get a relationship by grace_id. Returns edge dict or None."""
    escaped = escape_cypher_string(grace_id)
    query = f"MATCH ()-[r {{grace_id: '{escaped}'}}]->() RETURN r"
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        return None
    return _extract_edge(rows[0])
