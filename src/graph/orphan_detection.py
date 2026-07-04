"""Orphan vertex detection — finds vertices with zero edges.

Uses ArcadeDB SQL fallback since NOT (n)--() may not work in OpenCypher.
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.management_models import OrphanEntity, OrphanReport

logger = structlog.get_logger()


async def detect_orphans(client: ArcadeClient) -> OrphanReport:
    """Find vertices with zero edges (degree = 0).

    Excludes deprecated entities. Uses SQL fallback for orphan detection
    since ArcadeDB OpenCypher may not support NOT (n)--() syntax.

    F-0003 / ISS-0043 capture-the-why: this previously queried the generic
    ``V`` supertype (``SELECT ... FROM V WHERE bothE().size() = 0`` plus a
    ``FROM V`` total count). ArcadeDB does not auto-create a base ``V``
    class in this deployment, so on a V-less database
    ``GET /api/graph/management/orphans`` 500'd with ``Type with name 'V'
    was not found``. Mirrors the fixed ``health_metrics.get_health_report()``
    pattern: enumerate concrete vertex types from ``schema:types`` and run
    the same orphan/count probes per type; a schema-less database returns a
    well-formed empty report + one INFO instead of a 500. Response shape
    (``OrphanReport``) is unchanged.
    """
    types_result = await client.execute_sql("SELECT name, type FROM schema:types")
    vertex_names = [
        row["name"]
        for row in types_result.get("result", [])
        if row.get("type") == "vertex" and row.get("name")
    ]

    if not vertex_names:
        # Schema not yet synced — quiet, well-formed empty report rather
        # than a 500 (F-0003 / ISS-0043).
        logger.info("orphan_detection.schema_not_yet_synced")
        return OrphanReport(
            orphan_count=0,
            total_entities=0,
            orphan_rate=0.0,
            orphans=[],
        )

    orphans: list[OrphanEntity] = []
    total_entities = 0

    for type_name in vertex_names:
        # Backtick-quote the type name; names come from schema:types
        # (server-controlled), not from caller input (F-0003 / ISS-0043).
        quoted = f"`{type_name.replace('`', '')}`"

        orphan_result = await client.execute_sql(
            f"SELECT grace_id, name, extracted_at FROM {quoted} "
            "WHERE bothE().size() = 0 AND _deprecated = false"
        )
        for row in orphan_result.get("result", []):
            orphans.append(
                OrphanEntity(
                    grace_id=row.get("grace_id", ""),
                    # The concrete type being scanned IS the entity type —
                    # no @type projection needed on the per-type path.
                    entity_type=type_name,
                    name=row.get("name", ""),
                    created_at=row.get("extracted_at"),
                )
            )

        total_result = await client.execute_sql(
            f"SELECT count(*) AS cnt FROM {quoted} WHERE _deprecated = false"
        )
        total_rows = total_result.get("result", [])
        total_entities += total_rows[0].get("cnt", 0) if total_rows else 0

    orphan_count = len(orphans)
    orphan_rate = orphan_count / total_entities if total_entities > 0 else 0.0

    logger.info(
        "orphan_detection.complete",
        orphan_count=orphan_count,
        total_entities=total_entities,
        orphan_rate=round(orphan_rate, 4),
    )

    return OrphanReport(
        orphan_count=orphan_count,
        total_entities=total_entities,
        orphan_rate=orphan_rate,
        orphans=orphans,
    )
