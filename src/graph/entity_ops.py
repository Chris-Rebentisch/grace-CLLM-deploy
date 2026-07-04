"""Entity CRUD operations against ArcadeDB via OpenCypher.

All functions are async. All take an ArcadeClient instance.
All queries use language="opencypher" (never "cypher").
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import build_property_map, build_set_clause, escape_cypher_string
from src.graph.entity_models import (
    BulkInsertRequest,
    BulkInsertResponse,
    EntityCreate,
    EntityCreateResponse,
    EntityUpdate,
)
from src.graph.relationship_ops import insert_relationship

logger = structlog.get_logger()


def _extract_node(result_item: dict, alias: str = "n") -> dict:
    """Extract a node from an ArcadeDB result item.

    ArcadeDB may return the node directly or nested under the alias.
    """
    if alias in result_item and isinstance(result_item[alias], dict):
        return result_item[alias]
    return result_item


def _extract_rid(result_item: dict, alias: str = "n") -> str:
    """Extract @rid from an ArcadeDB result item, handling both direct and aliased."""
    rid = result_item.get("@rid")
    if rid:
        return rid
    nested = result_item.get(alias)
    if isinstance(nested, dict):
        return nested.get("@rid", "")
    return ""


async def canonical_lookup(
    client: ArcadeClient, entity_type: str, name: str | None,
) -> str | None:
    """Check if an entity with this type and name already exists.

    Returns grace_id if found, None otherwise.
    """
    if not name:
        return None
    escaped = escape_cypher_string(name)
    # Layer 2b (2026-06-10): match the canonical `name` OR any registered alias, so name
    # variants recorded via append_entity_alias dedup on insert.
    # F-28 residual (validation run, 2026-07-01): matching was case-SENSITIVE,
    # so "Riverbend Road tract" and "Riverbend Road Tract" landed as separate
    # vertices — which then split a sender's corroboration count across the
    # duplicates and defeated the promotion gate. Case-fold both sides
    # (ArcadeDB OpenCypher exposes toLower()) so case-variant names dedup at
    # insert time. `ANY(a IN null ...)` is null (falsey), so alias-less
    # entities still match on name alone.
    query = (
        f"MATCH (n:{entity_type}) "
        f"WHERE toLower(n.name) = toLower('{escaped}') "
        f"OR ANY(a IN n.aliases WHERE toLower(a) = toLower('{escaped}')) "
        f"RETURN n.grace_id LIMIT 1"
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        return None
    row = rows[0]
    # Result may be {"n.grace_id": "uuid"} or just the value
    if isinstance(row, dict):
        return row.get("n.grace_id") or row.get("grace_id")
    return None


async def append_entity_alias(
    client: ArcadeClient,
    grace_id: str,
    alias: str,
) -> bool:
    """Append alias to an entity's aliases array if not present.

    Stores aliases as a list of strings on the vertex property `aliases`.
    """
    if not alias.strip():
        return False
    escaped_id = escape_cypher_string(grace_id)
    escaped_alias = escape_cypher_string(alias.strip())
    fetch_query = f"MATCH (n {{grace_id: '{escaped_id}'}}) RETURN n.aliases AS aliases LIMIT 1"
    fetch_result = await client.execute_cypher(fetch_query)
    rows = fetch_result.get("result", [])
    if not rows:
        return False

    row = rows[0]
    existing = row.get("aliases", []) if isinstance(row, dict) else []
    if not isinstance(existing, list):
        existing = []
    if alias in existing:
        return False

    aliases = [*existing, alias]
    aliases_literal = "[" + ", ".join(
        f"'{escape_cypher_string(str(v))}'" for v in aliases if str(v).strip()
    ) + "]"
    update_query = (
        f"MATCH (n {{grace_id: '{escaped_id}'}}) "
        f"SET n.aliases = {aliases_literal} RETURN n.grace_id LIMIT 1"
    )
    await client.execute_cypher(update_query)
    return True


async def insert_entity(
    client: ArcadeClient,
    entity: EntityCreate,
    embedding: list[float] | None = None,
    superseded_by: str | None = None,
    corroboration_status: str | None = None,
    corroborating_sender_count: int | None = None,
) -> EntityCreateResponse:
    """Insert a single entity, with canonical dedup check on name.

    If an entity with the same type and name exists, returns it instead.

    Args:
        client: ArcadeDB client.
        entity: Entity to insert.
        embedding: Optional pre-computed 768-dim embedding vector. When
            provided, persisted via a post-insert SQL UPDATE on _embedding.
    """
    name = entity.properties.get("name")
    match_id = await canonical_lookup(client, entity.entity_type, name)

    if match_id:
        # Fetch existing entity to get RID
        query = f"MATCH (n {{grace_id: '{escape_cypher_string(match_id)}'}}) RETURN n"
        result = await client.execute_cypher(query)
        rows = result.get("result", [])
        rid = _extract_rid(rows[0]) if rows else ""
        # F-016 / ISS-0008 (validation run): the old canonical-match path
        # dropped the incoming property payload entirely — first-writer-wins,
        # so import ORDER silently determined property completeness (a
        # portfolio statement's shares/market_value never landed because a tax
        # memo created the vertex first). Fill-only merge: SET properties the
        # existing vertex lacks (absent or null); NEVER overwrite an existing
        # non-null value; never write nulls.
        existing = _extract_node(rows[0]) if rows else {}
        fill_props = {
            k: v
            for k, v in entity.properties.items()
            if v is not None and existing.get(k) is None
        }
        if fill_props:
            set_clause = build_set_clause("n", fill_props)
            merge_query = (
                f"MATCH (n {{grace_id: '{escape_cypher_string(match_id)}'}}) "
                f"{set_clause} RETURN n.grace_id LIMIT 1"
            )
            await client.execute_cypher(merge_query)
            logger.info(
                "entity.fill_only_merge",
                entity_type=entity.entity_type,
                grace_id=match_id,
                filled=sorted(fill_props),
            )
        logger.info(
            "entity.canonical_match",
            entity_type=entity.entity_type,
            name=name,
            grace_id=match_id,
        )
        return EntityCreateResponse(
            grace_id=match_id,
            rid=rid,
            entity_type=entity.entity_type,
            created=False,
            canonical_match=True,
        )

    grace_id = str(uuid4())
    now = datetime.now(UTC)

    # Build full property dict
    all_props: dict = {"grace_id": grace_id}
    all_props.update(entity.properties)
    all_props.update({
        "valid_from": entity.valid_from,
        "valid_to": entity.valid_to,
        "extracted_at": now,
        "extraction_confidence": entity.extraction_confidence,
        "source_document_id": entity.source_document_id,
        "extraction_event_id": entity.extraction_event_id,
        "schema_version": entity.schema_version,
        "ontology_module": entity.ontology_module,
        "human_validated": entity.human_validated,
        "evidence_origin": entity.evidence_origin,
        # D519 — sensitivity_tags vertex property for privilege governance.
        "sensitivity_tags": entity.sensitivity_tags,
        # D514 — additive `superseded_by` kwarg on vertex write; mirrors `evidence_origin` pattern.
        "superseded_by": superseded_by,
        # D517 — per-entity corroboration trust label and sender count.
        "corroboration_status": corroboration_status,
        "corroborating_sender_count": corroborating_sender_count,
        "_deprecated": False,
    })

    prop_map = build_property_map(all_props)
    query = f"CREATE (n:{entity.entity_type} {prop_map}) RETURN n"
    result = await client.execute_cypher(query)

    rows = result.get("result", [])
    rid = _extract_rid(rows[0]) if rows else ""

    # D445.4 / D356 — post-insert SQL UPDATE for vector; format_cypher_value
    # cannot serialize list values in the OpenCypher CREATE property map.
    # Authorization: D445.4.
    if embedding is not None:
        embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        escaped_gid = escape_cypher_string(grace_id)
        embed_sql = (
            f"UPDATE {entity.entity_type} SET _embedding = {embedding_literal} "
            f"WHERE grace_id = '{escaped_gid}'"
        )
        try:
            await client.execute_sql(embed_sql)
        except Exception as exc:
            logger.warning(
                "entity.embedding_write_failed",
                grace_id=grace_id,
                error=str(exc),
            )

    logger.info(
        "entity.created",
        entity_type=entity.entity_type,
        grace_id=grace_id,
        rid=rid,
    )
    return EntityCreateResponse(
        grace_id=grace_id,
        rid=rid,
        entity_type=entity.entity_type,
        created=True,
        canonical_match=False,
    )


async def get_entity(client: ArcadeClient, grace_id: str) -> dict | None:
    """Get an entity by grace_id. Returns full entity dict or None."""
    escaped = escape_cypher_string(grace_id)
    query = f"MATCH (n {{grace_id: '{escaped}'}}) RETURN n"
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        return None
    return _extract_node(rows[0])


async def update_entity(
    client: ArcadeClient, grace_id: str, update: EntityUpdate,
) -> dict:
    """Partial update of entity properties. Raises ValueError if not found."""
    escaped = escape_cypher_string(grace_id)
    set_clause = build_set_clause("n", update.properties)
    if not set_clause:
        # Nothing to update — just fetch and return
        entity = await get_entity(client, grace_id)
        if entity is None:
            raise ValueError(f"Entity not found: {grace_id}")
        return entity

    query = f"MATCH (n {{grace_id: '{escaped}'}}) {set_clause} RETURN n"
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        raise ValueError(f"Entity not found: {grace_id}")
    return _extract_node(rows[0])


async def bulk_insert(
    client: ArcadeClient, request: BulkInsertRequest,
) -> BulkInsertResponse:
    """Bulk insert entities and relationships with partial success support."""
    response = BulkInsertResponse()

    for entity in request.entities:
        # Copy batch-level IDs if not set on the entity
        if request.extraction_event_id and entity.extraction_event_id is None:
            entity.extraction_event_id = request.extraction_event_id
        if request.source_document_id and entity.source_document_id is None:
            entity.source_document_id = request.source_document_id

        try:
            result = await insert_entity(client, entity)
            response.entity_results.append(result)
            if result.created:
                response.entities_created += 1
            else:
                response.entities_matched += 1
        except Exception as exc:
            response.entities_failed += 1
            error_detail = {
                "error": str(exc),
                "entity_type": entity.entity_type,
                "name": entity.properties.get("name"),
            }
            response.entity_results.append(error_detail)
            response.errors.append(error_detail)
            logger.warning(
                "bulk_insert.entity_failed",
                entity_type=entity.entity_type,
                error=str(exc),
            )

    for rel in request.relationships:
        # Copy batch-level IDs if not set
        if request.extraction_event_id and rel.extraction_event_id is None:
            rel.extraction_event_id = request.extraction_event_id
        if request.source_document_id and rel.source_document_id is None:
            rel.source_document_id = request.source_document_id

        try:
            result = await insert_relationship(client, rel)
            response.relationship_results.append(result)
            response.relationships_created += 1
        except Exception as exc:
            response.relationships_failed += 1
            error_detail = {
                "error": str(exc),
                "relationship_type": rel.relationship_type,
                "source_grace_id": rel.source_grace_id,
                "target_grace_id": rel.target_grace_id,
            }
            response.relationship_results.append(error_detail)
            response.errors.append(error_detail)
            logger.warning(
                "bulk_insert.relationship_failed",
                relationship_type=rel.relationship_type,
                error=str(exc),
            )

    return response
