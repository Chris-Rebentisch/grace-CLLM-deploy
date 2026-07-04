"""Graph health statistics — aggregate metrics for the knowledge graph.

Uses ArcadeDB SQL for aggregate queries since OpenCypher aggregate support
may be limited in ArcadeDB.
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.management_models import GraphHealthReport, TypeCount

logger = structlog.get_logger()


async def get_edge_aggregation(
    client: ArcadeClient, edge_type: str, direction: str = "in"
) -> dict:
    """Ranked count of one edge type grouped by the node at one end.

    Domain-agnostic GROUP-BY for "which X has the most Y" questions:
    ``direction="in"`` groups by the edge *target* (e.g. ``governed_by`` +
    ``in`` ranks the jurisdictions most agreements point to);
    ``direction="out"`` groups by the edge *source* (e.g. ``party_to`` +
    ``out`` ranks the entities party to the most agreements). The
    ``edge_type`` is validated against ``schema:types`` before it is
    interpolated, so the call is injection-safe and self-documents the
    available edge types on a miss. Uses Cypher ``WITH … count(*)``
    grouping (ArcadeDB's implicit GROUP BY collapses to a single row, so
    the explicit ``WITH`` form is required); rows whose grouping node has
    no ``name`` (system vertices) are dropped.
    """
    types_result = await client.execute_sql("SELECT name, type FROM schema:types")
    edge_names = {
        r["name"]
        for r in types_result.get("result", [])
        if r.get("type") == "edge"
    }
    if edge_type not in edge_names:
        return {
            "edge_type": edge_type,
            "direction": direction,
            "error": "unknown_edge_type",
            "available_edge_types": sorted(edge_names),
            "counts": {},
        }
    if direction not in ("in", "out"):
        direction = "in"
    group_node = "b" if direction == "in" else "a"
    cypher = (
        f"MATCH (a)-[r:`{edge_type}`]->(b) "
        f"WITH {group_node}.name AS name, count(*) AS cnt "
        f"RETURN name, cnt ORDER BY cnt DESC"
    )
    result = await client.execute_cypher(cypher)
    counts = {
        row.get("name"): row.get("cnt")
        for row in result.get("result", [])
        if row.get("name")
    }
    logger.info(
        "edge_aggregation.complete",
        edge_type=edge_type,
        direction=direction,
        group_count=len(counts),
    )
    return {
        "edge_type": edge_type,
        "direction": direction,
        "total_edges": sum(counts.values()),
        "counts": counts,
    }


# Edge types that are graph plumbing / federation meta, not domain
# relationships — excluded from the completeness report.
_NON_DOMAIN_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "retrieved_from",
        "produced_by",
        "derives_from",
        "Bridge_Entity",
        "Cross_System_Reference",
    }
)


async def get_relationship_coverage(client: ArcadeClient) -> dict:
    """Schema-driven, domain-agnostic relationship-completeness report.

    For every domain relationship type in the graph, reports how many of
    its source entities actually carry the edge — the one-call "where is
    my extraction thin?" view. Coverage is a completeness metric, not a
    confidence score. Works for any ontology: it enumerates concrete edge
    types from ``schema:types`` and derives each one's source type from
    the graph (``labels(a)[0]``), so nothing domain-specific is assumed.
    Rows are sorted thinnest-coverage-first (most actionable). Graph
    plumbing / federation meta edges (``_NON_DOMAIN_EDGE_TYPES``) are
    excluded.
    """
    counts = await get_graph_counts(client)
    entity_counts: dict[str, int] = counts.get("entity_counts", {})

    types_result = await client.execute_sql("SELECT name, type FROM schema:types")
    edge_names = [
        r["name"]
        for r in types_result.get("result", [])
        if r.get("type") == "edge" and r["name"] not in _NON_DOMAIN_EDGE_TYPES
    ]

    rows: list[dict] = []
    for edge_type in edge_names:
        res = await client.execute_cypher(
            f"MATCH (a)-[r:`{edge_type}`]->(b) "
            f"WITH labels(a)[0] AS src_type, "
            f"count(DISTINCT a) AS srcs, count(r) AS edges "
            f"RETURN src_type, srcs, edges"
        )
        for row in res.get("result", []):
            src_type = row.get("src_type")
            srcs = row.get("srcs") or 0
            if not src_type or not srcs:
                continue
            total = entity_counts.get(src_type, 0)
            coverage = round(100.0 * srcs / total, 1) if total else None
            rows.append(
                {
                    "relationship": edge_type,
                    "source_type": src_type,
                    "source_total": total,
                    "sources_with_edge": srcs,
                    "coverage_pct": coverage,
                    "edge_count": row.get("edges") or 0,
                }
            )

    rows.sort(
        key=lambda r: r["coverage_pct"] if r["coverage_pct"] is not None else 999.0
    )
    logger.info("relationship_coverage.complete", relationship_count=len(rows))
    return {"relationships": rows}


async def get_graph_counts(client: ArcadeClient) -> dict:
    """Per-type entity and relationship counts for the whole graph.

    Enumerates concrete types from ``schema:types`` and runs a
    ``count(*)`` per type. This deliberately avoids ``SELECT FROM V`` /
    ``FROM E`` (the path :func:`get_health_report` used before ISS-0043
    closed): databases whose vertex/edge types are not registered under
    the generic ``V``/``E`` supertypes raise ``SchemaException`` on
    those queries. Iterating concrete types is supertype-independent.

    Returns a plain dict with totals and two name→count maps (nonzero
    only, sorted descending), so the count of any type — e.g. how many
    ``Agreement`` vertices exist — is an exact graph-wide figure rather
    than a retrieval-capped estimate.
    """
    types_result = await client.execute_sql("SELECT name, type FROM schema:types")
    rows = types_result.get("result", [])
    vertex_names = [r["name"] for r in rows if r.get("type") == "vertex"]
    edge_names = [r["name"] for r in rows if r.get("type") == "edge"]

    async def _count(type_name: str) -> int:
        # Backtick-quote the type name; names come from schema:types
        # (server-controlled), not from caller input.
        res = await client.execute_sql(
            f"SELECT count(*) AS cnt FROM `{type_name}`"
        )
        rows_ = res.get("result", [])
        return rows_[0].get("cnt", 0) if rows_ else 0

    entity_counts: dict[str, int] = {}
    for name in vertex_names:
        c = await _count(name)
        if c:
            entity_counts[name] = c
    relationship_counts: dict[str, int] = {}
    for name in edge_names:
        c = await _count(name)
        if c:
            relationship_counts[name] = c

    entity_counts = dict(
        sorted(entity_counts.items(), key=lambda kv: kv[1], reverse=True)
    )
    relationship_counts = dict(
        sorted(relationship_counts.items(), key=lambda kv: kv[1], reverse=True)
    )

    logger.info(
        "graph_counts.complete",
        total_entities=sum(entity_counts.values()),
        total_relationships=sum(relationship_counts.values()),
        entity_type_count=len(entity_counts),
    )

    return {
        "total_entities": sum(entity_counts.values()),
        "total_relationships": sum(relationship_counts.values()),
        "entity_counts": entity_counts,
        "relationship_counts": relationship_counts,
    }


async def get_health_report(client: ArcadeClient) -> GraphHealthReport:
    """Compute aggregate graph health statistics.

    Uses SQL for aggregate queries (count, group by) for ArcadeDB compatibility.

    F-0003 / ISS-0043 capture-the-why: this previously queried the generic
    ``V`` / ``E`` supertypes (``SELECT count(*) FROM V`` etc.). ArcadeDB does
    not auto-create a base ``V`` class in this deployment, so on a V-less
    database ``GET /api/graph/management/health`` 500'd with ``Type with name
    'V' was not found``. Mirrors the fixed exporter pattern
    (``src/analytics/graph_health_exporter._collect_health_snapshot``):
    enumerate concrete types from ``schema:types`` and aggregate per-type
    ``count(*)`` / orphan / deprecated probes; a schema-less database returns
    a well-formed empty report instead of a 500. Response shape
    (``GraphHealthReport``) is unchanged — same fields, same meaning.
    """
    types_result = await client.execute_sql("SELECT name, type FROM schema:types")
    rows = types_result.get("result", [])
    vertex_names = [r["name"] for r in rows if r.get("type") == "vertex"]
    edge_names = [r["name"] for r in rows if r.get("type") == "edge"]

    if not vertex_names:
        # Schema not yet synced (no vertex types registered) — quiet,
        # well-formed empty report rather than a 500 (F-0003 / ISS-0043).
        logger.info("health_metrics.schema_not_yet_synced")
        return GraphHealthReport(
            total_vertices=0,
            total_edges=0,
            density=0.0,
            orphan_count=0,
            orphan_rate=0.0,
            avg_edges_per_vertex=0.0,
            vertex_types=[],
            edge_types=[],
            deprecated_vertices=0,
            deprecated_edges=0,
        )

    async def _count_one(sql: str) -> int:
        res = await client.execute_sql(sql)
        rows_ = res.get("result", [])
        return rows_[0].get("cnt", 0) if rows_ else 0

    # Per-concrete-type aggregation. Backtick-quote the type name; names
    # come from schema:types (server-controlled), not from caller input.
    vertex_types: list[TypeCount] = []
    orphan_count = 0
    deprecated_vertices = 0
    for name in vertex_names:
        cnt = await _count_one(f"SELECT count(*) AS cnt FROM `{name}`")
        if not cnt:
            continue
        vertex_types.append(TypeCount(type_name=name, count=cnt))
        # Same orphan predicate the old FROM-V path (orphan_detection)
        # used, applied per concrete type.
        orphan_count += await _count_one(
            f"SELECT count(*) AS cnt FROM `{name}` "
            "WHERE bothE().size() = 0 AND _deprecated = false"
        )
        deprecated_vertices += await _count_one(
            f"SELECT count(*) AS cnt FROM `{name}` WHERE _deprecated = true"
        )

    edge_types: list[TypeCount] = []
    deprecated_edges = 0
    for name in edge_names:
        cnt = await _count_one(f"SELECT count(*) AS cnt FROM `{name}`")
        if not cnt:
            continue
        edge_types.append(TypeCount(type_name=name, count=cnt))
        deprecated_edges += await _count_one(
            f"SELECT count(*) AS cnt FROM `{name}` WHERE _deprecated = true"
        )

    total_vertices = sum(tc.count for tc in vertex_types)
    total_edges = sum(tc.count for tc in edge_types)

    # Calculate derived metrics
    density = total_edges / total_vertices if total_vertices > 0 else 0.0
    avg_edges = total_edges / total_vertices if total_vertices > 0 else 0.0
    orphan_rate = orphan_count / total_vertices if total_vertices > 0 else 0.0

    logger.info(
        "health_metrics.complete",
        total_vertices=total_vertices,
        total_edges=total_edges,
        density=round(density, 4),
        orphan_rate=round(orphan_rate, 4),
    )

    return GraphHealthReport(
        total_vertices=total_vertices,
        total_edges=total_edges,
        density=density,
        orphan_count=orphan_count,
        orphan_rate=orphan_rate,
        avg_edges_per_vertex=avg_edges,
        vertex_types=vertex_types,
        edge_types=edge_types,
        deprecated_vertices=deprecated_vertices,
        deprecated_edges=deprecated_edges,
    )
