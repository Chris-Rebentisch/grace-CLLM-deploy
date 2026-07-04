"""ArcadeDB → Prometheus graph-health exporter (Chunk 25 §4).

Scheduled asyncio task. Each tick collects a graph-health snapshot,
applies a ``top-N + _other_`` cap to the ``entity_type`` /
``relationship_type`` labels, and publishes five gauges via the OTel
Meter API. On ArcadeDB failure it catches, logs a warning, and skips
the tick — the ``grace_graph_exporter_last_success_seconds`` freshness
gauge is updated on success only (so Grafana can visibly degrade).

F-0003 / ISS-0043: the snapshot is collected here via per-concrete-type
queries enumerated from ``schema:types`` instead of
``health_metrics.get_health_report()``'s ``SELECT ... FROM V`` path.
ArcadeDB does not auto-create a base ``V`` class, so on a V-less
database the old path raised ``Type with name 'V' was not found`` EVERY
tick, permanently (873 of 874 ERROR lines in the validation run),
drowning the error channel the monitoring mesh scans. When the schema
has no vertex types yet, the exporter logs a SINGLE INFO and skips
quietly until types appear.

Task lifecycle is owned by the FastAPI ``lifespan`` in
``src/api/main.py`` (D174) — ``setup_otel`` stays sync.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import structlog

from src.analytics import metrics as grace_metrics
from src.graph.arcade_client import ArcadeClient
from src.graph.management_models import GraphHealthReport, TypeCount

log = structlog.get_logger(__name__)

_OTHER = "_other_"

# F-0003 / ISS-0043: one-shot INFO state flag for the "schema not yet
# synced" (no vertex types registered) condition. Reset when types appear
# so a later re-emptied schema logs once again.
_schema_not_synced_logged = False


def _top_n_with_other(
    items: list[tuple[str, int]], topn: int
) -> list[tuple[str, int]]:
    """Keep the top-N items by count; aggregate the remainder into ``_other_``."""
    if topn <= 0 or len(items) <= topn:
        return list(items)
    ordered = sorted(items, key=lambda pair: pair[1], reverse=True)
    head = ordered[:topn]
    tail_sum = sum(cnt for _, cnt in ordered[topn:])
    if tail_sum > 0:
        head.append((_OTHER, tail_sum))
    return head


def _publish_type_gauge(
    gauge,
    label_name: str,
    counts: list[tuple[str, int]],
    topn: int,
) -> None:
    """Emit one value per top-N entry; tail aggregates into ``_other_``."""
    for name, count in _top_n_with_other(counts, topn):
        gauge.set(count, attributes={label_name: name})


async def _count_one(client: ArcadeClient, sql: str) -> int:
    """Run a single ``count(*) AS cnt`` SQL query and return the count."""
    res = await client.execute_sql(sql)
    rows = res.get("result", [])
    return rows[0].get("cnt", 0) if rows else 0


async def _collect_health_snapshot(
    client: ArcadeClient,
) -> GraphHealthReport | None:
    """V-less graph-health snapshot (F-0003 / ISS-0043).

    Enumerates concrete types from ``schema:types`` and aggregates
    per-type ``count(*)`` figures — never queries the generic ``V`` /
    ``E`` supertypes, which do not exist on databases whose types were
    created without them. Returns ``None`` (after a one-shot INFO) when
    the schema carries no vertex types yet.
    """
    global _schema_not_synced_logged

    types_result = await client.execute_sql(
        "SELECT name, type FROM schema:types"
    )
    rows = types_result.get("result", [])
    vertex_names = [r["name"] for r in rows if r.get("type") == "vertex"]
    edge_names = [r["name"] for r in rows if r.get("type") == "edge"]

    if not vertex_names:
        # F-0003 / ISS-0043: no vertex types registered — log once at
        # INFO (not per-tick ERROR) and skip quietly until types appear.
        if not _schema_not_synced_logged:
            log.info("graph_health_exporter.schema_not_yet_synced")
            _schema_not_synced_logged = True
        return None
    _schema_not_synced_logged = False

    vertex_types: list[TypeCount] = []
    orphan_count = 0
    for name in vertex_names:
        # Backtick-quote the type name; names come from schema:types
        # (server-controlled), not from caller input.
        cnt = await _count_one(
            client, f"SELECT count(*) AS cnt FROM `{name}`"
        )
        if cnt:
            vertex_types.append(TypeCount(type_name=name, count=cnt))
            # Per-type orphan probe (same predicate the old FROM-V path
            # used in orphan_detection, applied per concrete type).
            orphan_count += await _count_one(
                client,
                f"SELECT count(*) AS cnt FROM `{name}` "
                "WHERE bothE().size() = 0 AND _deprecated = false",
            )

    edge_types: list[TypeCount] = []
    for name in edge_names:
        cnt = await _count_one(
            client, f"SELECT count(*) AS cnt FROM `{name}`"
        )
        if cnt:
            edge_types.append(TypeCount(type_name=name, count=cnt))

    total_vertices = sum(tc.count for tc in vertex_types)
    total_edges = sum(tc.count for tc in edge_types)
    density = total_edges / total_vertices if total_vertices > 0 else 0.0

    return GraphHealthReport(
        total_vertices=total_vertices,
        total_edges=total_edges,
        density=density,
        orphan_count=orphan_count,
        orphan_rate=(
            orphan_count / total_vertices if total_vertices > 0 else 0.0
        ),
        avg_edges_per_vertex=density,
        vertex_types=vertex_types,
        edge_types=edge_types,
        # Deprecated tallies are not published by this exporter's five
        # gauges; not worth an extra 2×N queries per tick.
        deprecated_vertices=0,
        deprecated_edges=0,
    )


async def _publish_one_tick(
    client: ArcadeClient, topn: int
) -> None:
    """Single-tick emission: collects the snapshot and publishes gauges."""
    report = await _collect_health_snapshot(client)
    if report is None:
        # Schema not yet synced — nothing to publish; freshness gauge is
        # deliberately NOT bumped (success-only semantics preserved).
        return

    _publish_type_gauge(
        grace_metrics.graph_node_count,
        "entity_type",
        [(tc.type_name, tc.count) for tc in report.vertex_types],
        topn,
    )
    _publish_type_gauge(
        grace_metrics.graph_edge_count,
        "relationship_type",
        [(tc.type_name, tc.count) for tc in report.edge_types],
        topn,
    )
    grace_metrics.graph_orphan_node_count.set(report.orphan_count)
    grace_metrics.graph_density.set(report.density)
    grace_metrics.graph_exporter_last_success_seconds.set(time.time())


async def graph_health_exporter_task(
    client_factory: Callable[[], ArcadeClient],
    interval_seconds: int,
    topn: int,
    stop_event: asyncio.Event,
) -> None:
    """Tick loop: read health report, publish gauges, honor stop_event."""
    log.info(
        "graph_health_exporter.start",
        interval_seconds=interval_seconds,
        topn=topn,
    )
    while not stop_event.is_set():
        try:
            client = client_factory()
            await _publish_one_tick(client, topn)
        except Exception as exc:
            log.warning("graph_health_exporter.tick_failed", error=str(exc))

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue

    log.info("graph_health_exporter.stop")
