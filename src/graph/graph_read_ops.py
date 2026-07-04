"""Chunk 28 D212 — cursor-paged read operations for graph entities/relationships.

Cursor encoding: opaque base64(JSON({"after_rid": str, "filter_fingerprint": str})).
The opaque field `after_rid` is treated as a string offset by the listing
queries. If filters change mid-pagination, the embedded fingerprint will
mismatch and the route returns 400 (`filter_mismatch`).
"""

from __future__ import annotations

import base64
import hashlib
import json

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.graph_read_models import (
    EntityRecord,
    PagedEntitiesResponse,
    PagedRelationshipsResponse,
    RelationshipRecord,
)

logger = structlog.get_logger()


# ---------- Cursor encode / decode / fingerprint ----------


def _encode_cursor(after_rid: str, filter_fingerprint: str) -> str:
    payload = json.dumps(
        {"after_rid": after_rid, "filter_fingerprint": filter_fingerprint},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode an opaque cursor. Raises ValueError on malformed input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Malformed cursor: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Cursor payload is not an object")
    if "after_rid" not in data or "filter_fingerprint" not in data:
        raise ValueError("Cursor missing required fields")
    after_rid = data["after_rid"]
    fp = data["filter_fingerprint"]
    if not isinstance(after_rid, str) or not isinstance(fp, str):
        raise ValueError("Cursor field types invalid")
    return after_rid, fp


def _compute_filter_fingerprint(
    entity_type: str | None, ontology_module: str | None
) -> str:
    canonical = json.dumps(
        {"entity_type": entity_type, "ontology_module": ontology_module},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_relationship_fingerprint(relationship_type: str | None) -> str:
    canonical = json.dumps(
        {"relationship_type": relationship_type},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------- Vertex / edge → record adapters ----------


def _vertex_to_entity_record(node: dict) -> EntityRecord:
    """Adapt an ArcadeDB vertex dict into the stable EntityRecord shape.

    Promotion rules: known stable fields go into named columns; everything
    else stays in `properties`. ArcadeDB internals (`@rid`, `@type`, etc.)
    are dropped so the wire payload only carries domain data.
    """
    promoted = {
        "grace_id",
        "source_document_id",
        "extraction_event_id",
        "ontology_module",
        "human_validated",
        "valid_from",
        "valid_to",
        "extraction_confidence",
    }
    arcade_internal = {
        "@rid",
        "@type",
        "@cat",
        "@in",
        "@out",
    }
    entity_type = node.get("@type") or "Unknown"
    properties = {
        k: v
        for k, v in node.items()
        if k not in promoted and k not in arcade_internal and k != "_deprecated"
    }
    return EntityRecord(
        grace_id=node.get("grace_id", ""),
        entity_type=entity_type,
        properties=properties,
        source_document_id=node.get("source_document_id"),
        extraction_event_id=node.get("extraction_event_id"),
        ontology_module=node.get("ontology_module"),
        human_validated=bool(node.get("human_validated", False)),
        valid_from=node.get("valid_from"),
        valid_to=node.get("valid_to"),
        extraction_confidence=node.get("extraction_confidence"),
    )


def _row_to_relationship_record(row: dict) -> RelationshipRecord:
    """Adapt a list-relationships row into the stable RelationshipRecord shape."""
    edge = row.get("r") if isinstance(row.get("r"), dict) else row
    rel_type = (
        row.get("relationship_type")
        or edge.get("@type")
        or "related_to"
    )
    arcade_internal = {"@rid", "@type", "@cat", "@in", "@out"}
    promoted = {
        "grace_id",
        "source_document_id",
        "extraction_event_id",
        "ontology_module",
        "human_validated",
        "extraction_confidence",
    }
    properties = {
        k: v
        for k, v in edge.items()
        if k not in promoted and k not in arcade_internal
    }
    return RelationshipRecord(
        grace_id=edge.get("grace_id", ""),
        relationship_type=rel_type,
        source_grace_id=row.get("source_grace_id", ""),
        target_grace_id=row.get("target_grace_id", ""),
        properties=properties,
        source_document_id=edge.get("source_document_id"),
        extraction_event_id=edge.get("extraction_event_id"),
        ontology_module=edge.get("ontology_module"),
        human_validated=bool(edge.get("human_validated", False)),
        extraction_confidence=edge.get("extraction_confidence"),
    )


# ---------- List queries ----------


def _build_entity_list_query(
    entity_type: str | None,
    ontology_module: str | None,
    skip: int,
    limit: int,
) -> str:
    label = f":{entity_type}" if entity_type else ""
    where_clauses = ["n._deprecated = false"]
    if ontology_module:
        # Escape single quotes by replacing with doubled single quotes for
        # OpenCypher string literals.
        escaped = ontology_module.replace("'", "''")
        where_clauses.append(f"n.ontology_module = '{escaped}'")
    where_clause = " AND ".join(where_clauses)
    return (
        f"MATCH (n{label}) "
        f"WHERE {where_clause} "
        f"RETURN n "
        f"SKIP {skip} "
        f"LIMIT {limit}"
    )


def _build_relationship_list_query(
    relationship_type: str | None,
    skip: int,
    limit: int,
) -> str:
    rel_label = f":{relationship_type}" if relationship_type else ""
    return (
        f"MATCH (a)-[r{rel_label}]->(b) "
        f"RETURN a.grace_id AS source_grace_id, "
        f"b.grace_id AS target_grace_id, "
        f"type(r) AS relationship_type, "
        f"r "
        f"SKIP {skip} "
        f"LIMIT {limit}"
    )


async def list_entities_paged(
    client: ArcadeClient,
    cursor: str | None,
    limit: int,
    entity_type: str | None,
    ontology_module: str | None,
) -> PagedEntitiesResponse:
    """Return one page of entities with an opaque cursor for the next page."""
    current_fingerprint = _compute_filter_fingerprint(entity_type, ontology_module)
    skip = 0
    if cursor:
        after_rid, embedded_fp = _decode_cursor(cursor)
        if embedded_fp != current_fingerprint:
            raise FilterMismatchError(
                "Filters changed mid-pagination; reset to page 1"
            )
        try:
            skip = int(after_rid)
        except ValueError as exc:
            raise ValueError(f"Cursor after_rid is not an integer offset: {exc}") from exc

    # Request limit+1 so we can detect whether more results exist.
    query = _build_entity_list_query(
        entity_type, ontology_module, skip, limit + 1
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    has_more = len(rows) > limit
    page = rows[:limit]

    entities: list[EntityRecord] = []
    for row in page:
        node = row.get("n") if isinstance(row, dict) and "n" in row else row
        if isinstance(node, dict):
            entities.append(_vertex_to_entity_record(node))

    next_cursor: str | None = None
    if has_more:
        next_cursor = _encode_cursor(str(skip + limit), current_fingerprint)

    return PagedEntitiesResponse(entities=entities, next_cursor=next_cursor)


async def list_relationships_paged(
    client: ArcadeClient,
    cursor: str | None,
    limit: int,
    relationship_type: str | None,
) -> PagedRelationshipsResponse:
    """Return one page of relationships with an opaque cursor for the next page."""
    current_fingerprint = _compute_relationship_fingerprint(relationship_type)
    skip = 0
    if cursor:
        after_rid, embedded_fp = _decode_cursor(cursor)
        if embedded_fp != current_fingerprint:
            raise FilterMismatchError(
                "Filters changed mid-pagination; reset to page 1"
            )
        try:
            skip = int(after_rid)
        except ValueError as exc:
            raise ValueError(f"Cursor after_rid is not an integer offset: {exc}") from exc

    query = _build_relationship_list_query(
        relationship_type, skip, limit + 1
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    has_more = len(rows) > limit
    page = rows[:limit]

    relationships: list[RelationshipRecord] = []
    for row in page:
        if isinstance(row, dict):
            relationships.append(_row_to_relationship_record(row))

    next_cursor: str | None = None
    if has_more:
        next_cursor = _encode_cursor(str(skip + limit), current_fingerprint)

    return PagedRelationshipsResponse(
        relationships=relationships, next_cursor=next_cursor
    )


class FilterMismatchError(ValueError):
    """Raised when a paginated cursor's embedded fingerprint disagrees with the active filters."""
