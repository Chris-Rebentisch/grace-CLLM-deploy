"""Temporal windowed graph view — read-only filtered view by time range.

Returns entities and relationships valid within a specified time window.
Does not modify the graph.
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import format_cypher_value
from src.graph.management_models import TemporalWindowRequest, TemporalWindowResponse

logger = structlog.get_logger()


async def get_temporal_window(
    client: ArcadeClient,
    request: TemporalWindowRequest,
) -> TemporalWindowResponse:
    """Return entities and relationships valid within a time window.

    Read-only view. Does not modify the graph.
    Uses per-type queries if entity_types is specified, otherwise queries all vertices.
    """
    start_val = format_cypher_value(request.start)
    end_val = format_cypher_value(request.end)

    entities: list[dict] = []

    if request.entity_types:
        # Query per type to avoid labels() function issues in ArcadeDB
        for etype in request.entity_types:
            query = (
                f"MATCH (n:{etype}) "
                f"WHERE n._deprecated = false "
                f"AND (n.valid_from IS NULL OR n.valid_from <= {end_val}) "
                f"AND (n.valid_to IS NULL OR n.valid_to >= {start_val}) "
                f"RETURN n "
                f"LIMIT {request.limit}"
            )
            result = await client.execute_cypher(query)
            for row in result.get("result", []):
                node = row.get("n", row) if isinstance(row, dict) else row
                entities.append(node)
    else:
        # Query all vertex types
        query = (
            f"MATCH (n) "
            f"WHERE n._deprecated = false "
            f"AND (n.valid_from IS NULL OR n.valid_from <= {end_val}) "
            f"AND (n.valid_to IS NULL OR n.valid_to >= {start_val}) "
            f"RETURN n "
            f"LIMIT {request.limit}"
        )
        result = await client.execute_cypher(query)
        for row in result.get("result", []):
            node = row.get("n", row) if isinstance(row, dict) else row
            entities.append(node)

    # Collect grace_ids from result set for relationship filtering
    entity_ids = {
        e.get("grace_id") for e in entities if isinstance(e, dict) and e.get("grace_id")
    }

    relationships: list[dict] = []

    if request.include_relationships and entity_ids:
        # Query relationships between entities in the window
        # Build IN list for grace_ids
        id_list = ", ".join(f"'{gid}'" for gid in entity_ids)
        rel_query = (
            f"MATCH (a)-[r]->(b) "
            f"WHERE r._deprecated = false "
            f"AND (r.valid_from IS NULL OR r.valid_from <= {end_val}) "
            f"AND (r.valid_to IS NULL OR r.valid_to >= {start_val}) "
            f"AND a.grace_id IN [{id_list}] "
            f"AND b.grace_id IN [{id_list}] "
            f"RETURN a.grace_id AS source, type(r) AS rel_type, r, b.grace_id AS target"
        )
        rel_result = await client.execute_cypher(rel_query)
        for row in rel_result.get("result", []):
            rel_data = row.get("r", {}) if isinstance(row, dict) else {}
            relationships.append({
                "source": row.get("source", ""),
                "target": row.get("target", ""),
                "type": row.get("rel_type", rel_data.get("@type", "")),
                **{k: v for k, v in rel_data.items() if not k.startswith("@")},
            })

    logger.info(
        "temporal_window.complete",
        entity_count=len(entities),
        relationship_count=len(relationships),
    )

    return TemporalWindowResponse(
        window_start=request.start,
        window_end=request.end,
        entities=entities,
        relationships=relationships,
        entity_count=len(entities),
        relationship_count=len(relationships),
    )
