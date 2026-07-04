"""Strategy 1: Graph traversal via OpenCypher variable-length MATCH."""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RetrievalCandidate, RetrievalQuery

logger = structlog.get_logger()


def _extract_entity_from_row(row: dict) -> dict:
    """Extract entity fields from an ArcadeDB result row.

    ArcadeDB may return fields directly on the row or nested under alias 'm'.
    """
    if "m" in row and isinstance(row["m"], dict):
        return row["m"]
    return row


async def graph_search(
    client: ArcadeClient,
    query: RetrievalQuery,
    config: RetrievalConfig,
    temporal_filter: bool = True,
) -> list[RetrievalCandidate]:
    """Run graph traversal strategy.

    If seed_entity_ids provided: variable-length traversal from each seed.
    If no seeds: fall back to name CONTAINS search.

    When temporal_filter=True and query has temporal bounds, adds WHERE clause.
    When temporal_as_strategy=True in config, temporal_filter should be False
    (temporal runs as separate strategy).
    """
    # Skip temporal WHERE if temporal is running as a separate strategy
    apply_temporal = temporal_filter and not config.temporal_as_strategy

    candidates: list[RetrievalCandidate] = []

    if query.seed_entity_ids:
        for seed_id in query.seed_entity_ids:
            seed_candidates = await _traversal_from_seed(
                client, seed_id, query, config, apply_temporal
            )
            candidates.extend(seed_candidates)
    else:
        candidates = await _name_search(client, query, config, apply_temporal)

    # Deduplicate by grace_id, keeping first occurrence (closest hop)
    seen: set[str] = set()
    deduped: list[RetrievalCandidate] = []
    for c in candidates:
        if c.grace_id not in seen:
            seen.add(c.grace_id)
            deduped.append(c)

    # Assign ranks
    for i, c in enumerate(deduped):
        c.rank = i + 1

    return deduped[: config.graph_result_limit]


async def _traversal_from_seed(
    client: ArcadeClient,
    seed_id: str,
    query: RetrievalQuery,
    config: RetrievalConfig,
    apply_temporal: bool,
) -> list[RetrievalCandidate]:
    """Variable-length traversal from a single seed entity."""
    escaped_id = escape_cypher_string(seed_id)
    depth = config.max_hop_depth

    where_parts = ["m._deprecated = false"]
    if apply_temporal:
        if query.temporal_start:
            ts = query.temporal_start.isoformat()
            where_parts.append(
                f"(m.valid_from IS NULL OR m.valid_from <= '{ts}')"
            )
        if query.temporal_end:
            te = query.temporal_end.isoformat()
            where_parts.append(
                f"(m.valid_to IS NULL OR m.valid_to >= '{te}')"
            )
    if query.entity_types:
        # OpenCypher label filter — filter in WHERE via @type
        type_list = ", ".join(f"'{escape_cypher_string(t)}'" for t in query.entity_types)
        where_parts.append(f"m.`@type` IN [{type_list}]")

    where_clause = " AND ".join(where_parts)

    cypher = (
        f"MATCH (seed {{grace_id: '{escaped_id}'}})-[*1..{depth}]->(m) "
        f"WHERE {where_clause} "
        f"RETURN m "
        f"LIMIT {config.graph_result_limit}"
    )

    result = await client.execute_cypher(cypher)
    rows = result.get("result", [])

    candidates: list[RetrievalCandidate] = []
    for row in rows:
        entity = _extract_entity_from_row(row)
        gid = entity.get("grace_id", "")
        if not gid or gid == seed_id:
            continue
        candidates.append(
            RetrievalCandidate(
                grace_id=gid,
                entity_type=entity.get("@type", "Entity"),
                name=entity.get("name", ""),
                properties={
                    k: v for k, v in entity.items()
                    if k not in ("@rid", "@type", "@cat", "@in", "@out")
                },
                score=1.0,
                strategy="graph",
                hop_distance=1,  # Simplified — exact hop tracking requires path analysis
            )
        )

    return candidates


async def _name_search(
    client: ArcadeClient,
    query: RetrievalQuery,
    config: RetrievalConfig,
    apply_temporal: bool,
) -> list[RetrievalCandidate]:
    """Fallback: text search in entity names when no seed entities."""
    search_term = escape_cypher_string(query.query_text)

    where_parts = [
        f"m.name CONTAINS '{search_term}'",
        "m._deprecated = false",
    ]
    if apply_temporal:
        if query.temporal_start:
            ts = query.temporal_start.isoformat()
            where_parts.append(
                f"(m.valid_from IS NULL OR m.valid_from <= '{ts}')"
            )
        if query.temporal_end:
            te = query.temporal_end.isoformat()
            where_parts.append(
                f"(m.valid_to IS NULL OR m.valid_to >= '{te}')"
            )
    if query.entity_types:
        type_list = ", ".join(f"'{escape_cypher_string(t)}'" for t in query.entity_types)
        where_parts.append(f"m.`@type` IN [{type_list}]")

    where_clause = " AND ".join(where_parts)

    cypher = (
        f"MATCH (m) "
        f"WHERE {where_clause} "
        f"RETURN m "
        f"LIMIT {config.graph_result_limit}"
    )

    result = await client.execute_cypher(cypher)
    rows = result.get("result", [])

    candidates: list[RetrievalCandidate] = []
    for row in rows:
        entity = _extract_entity_from_row(row)
        gid = entity.get("grace_id", "")
        if not gid:
            continue
        candidates.append(
            RetrievalCandidate(
                grace_id=gid,
                entity_type=entity.get("@type", "Entity"),
                name=entity.get("name", ""),
                properties={
                    k: v for k, v in entity.items()
                    if k not in ("@rid", "@type", "@cat", "@in", "@out")
                },
                score=1.0,
                strategy="graph",
            )
        )

    return candidates
