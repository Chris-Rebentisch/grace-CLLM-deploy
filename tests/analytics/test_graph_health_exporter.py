"""Tests for ``src.analytics.graph_health_exporter`` (spec §10.2).

Updated for F-0003 / ISS-0043: the exporter no longer calls
``health_metrics.get_health_report()`` (whose ``SELECT ... FROM V`` path
raises ``Type with name 'V' was not found`` forever on databases without
the generic ``V`` supertype). Ticks now collect a V-less snapshot via
``_collect_health_snapshot`` (schema:types enumeration + per-type counts).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analytics import graph_health_exporter as ghe
from src.analytics import metrics as grace_metrics
from src.graph.management_models import GraphHealthReport, TypeCount


def _report(
    vertex_types: list[tuple[str, int]],
    edge_types: list[tuple[str, int]],
    total_vertices: int = 100,
    total_edges: int = 50,
    orphan_count: int = 3,
    density: float = 0.5,
) -> GraphHealthReport:
    return GraphHealthReport(
        total_vertices=total_vertices,
        total_edges=total_edges,
        density=density,
        orphan_count=orphan_count,
        orphan_rate=orphan_count / total_vertices if total_vertices else 0.0,
        avg_edges_per_vertex=total_edges / total_vertices if total_vertices else 0.0,
        vertex_types=[TypeCount(type_name=n, count=c) for n, c in vertex_types],
        edge_types=[TypeCount(type_name=n, count=c) for n, c in edge_types],
        deprecated_vertices=0,
        deprecated_edges=0,
    )


@pytest.fixture(autouse=True)
def _reset_schema_flag():
    """Reset the one-shot 'schema not synced' flag between tests."""
    ghe._schema_not_synced_logged = False
    yield
    ghe._schema_not_synced_logged = False


@pytest.mark.asyncio
async def test_tick_publishes_five_gauges():
    """One tick: five gauges receive values; last_success is updated."""
    mock_client = MagicMock()
    report = _report(
        vertex_types=[("Company", 100)],
        edge_types=[("OWNS", 50)],
    )

    with patch.object(
        ghe, "_collect_health_snapshot", AsyncMock(return_value=report)
    ), \
         patch.object(grace_metrics, "graph_node_count") as gnode, \
         patch.object(grace_metrics, "graph_edge_count") as gedge, \
         patch.object(grace_metrics, "graph_orphan_node_count") as gorph, \
         patch.object(grace_metrics, "graph_density") as gdens, \
         patch.object(
             grace_metrics, "graph_exporter_last_success_seconds"
         ) as glast:
        await ghe._publish_one_tick(mock_client, topn=20)

    gnode.set.assert_called_once()
    gedge.set.assert_called_once()
    gorph.set.assert_called_once_with(3)
    gdens.set.assert_called_once_with(0.5)
    glast.set.assert_called_once()
    last_value = glast.set.call_args[0][0]
    assert last_value > 0


@pytest.mark.asyncio
async def test_top_n_cap_collapses_tail_to_other():
    """N=3 + [100,50,25,10,5] => three top types + _other_ with 15."""
    mock_client = MagicMock()
    report = _report(
        vertex_types=[
            ("A", 100), ("B", 50), ("C", 25), ("D", 10), ("E", 5),
        ],
        edge_types=[("OWNS", 50)],
    )

    with patch.object(
        ghe, "_collect_health_snapshot", AsyncMock(return_value=report)
    ), \
         patch.object(grace_metrics, "graph_node_count") as gnode, \
         patch.object(grace_metrics, "graph_edge_count"), \
         patch.object(grace_metrics, "graph_orphan_node_count"), \
         patch.object(grace_metrics, "graph_density"), \
         patch.object(grace_metrics, "graph_exporter_last_success_seconds"):
        await ghe._publish_one_tick(mock_client, topn=3)

    emitted = {
        call.kwargs["attributes"]["entity_type"]: call.args[0]
        for call in gnode.set.call_args_list
    }
    assert emitted == {"A": 100, "B": 50, "C": 25, "_other_": 15}


@pytest.mark.asyncio
async def test_arcade_failure_does_not_update_freshness_or_crash():
    """Exception from the snapshot: log warning, skip tick, keep loop alive."""
    boom = AsyncMock(side_effect=RuntimeError("arcade down"))
    stop_event = asyncio.Event()

    def factory() -> object:
        return MagicMock()

    with patch.object(ghe, "_collect_health_snapshot", boom), \
         patch.object(
             grace_metrics, "graph_exporter_last_success_seconds"
         ) as glast:
        stop_event.set()
        await ghe.graph_health_exporter_task(
            client_factory=factory,
            interval_seconds=1,
            topn=20,
            stop_event=stop_event,
        )

    glast.set.assert_not_called()


# ---------------------------------------------------------------------------
# F-0003 / ISS-0043 — V-less snapshot collection
# ---------------------------------------------------------------------------


def _client_with_types(
    schema_rows: list[dict],
    counts: dict[str, int],
    orphans: dict[str, int] | None = None,
) -> MagicMock:
    """Mock ArcadeClient whose execute_sql answers by query shape."""
    orphans = orphans or {}
    executed: list[str] = []

    async def _execute_sql(sql: str, *args, **kwargs) -> dict:
        executed.append(sql)
        if "schema:types" in sql:
            return {"result": schema_rows}
        for name, cnt in orphans.items():
            if f"`{name}`" in sql and "bothE()" in sql:
                return {"result": [{"cnt": cnt}]}
        for name, cnt in counts.items():
            if f"`{name}`" in sql:
                return {"result": [{"cnt": cnt}]}
        return {"result": [{"cnt": 0}]}

    client = MagicMock()
    client.execute_sql = AsyncMock(side_effect=_execute_sql)
    client._executed = executed
    return client


@pytest.mark.asyncio
async def test_snapshot_enumerates_concrete_types_never_queries_v():
    """F-0003 / ISS-0043: counts come from schema:types enumeration; no FROM V/E."""
    client = _client_with_types(
        schema_rows=[
            {"name": "Company", "type": "vertex"},
            {"name": "Person", "type": "vertex"},
            {"name": "OWNS", "type": "edge"},
            {"name": "SomeDoc", "type": "document"},
        ],
        counts={"Company": 10, "Person": 5, "OWNS": 6},
        orphans={"Company": 1, "Person": 2},
    )

    report = await ghe._collect_health_snapshot(client)

    assert report is not None
    assert report.total_vertices == 15
    assert report.total_edges == 6
    assert report.orphan_count == 3
    assert report.density == pytest.approx(6 / 15)
    assert {tc.type_name: tc.count for tc in report.vertex_types} == {
        "Company": 10,
        "Person": 5,
    }
    assert {tc.type_name: tc.count for tc in report.edge_types} == {"OWNS": 6}
    # The defect class: the generic V/E supertypes must never be queried.
    for sql in client._executed:
        assert "FROM V" not in sql
        assert "FROM E" not in sql
    # Document types are not treated as vertex types.
    assert not any("`SomeDoc`" in sql for sql in client._executed)


@pytest.mark.asyncio
async def test_empty_schema_logs_single_info_and_skips_quietly():
    """No vertex types: one INFO across many ticks, nothing published, no ERROR."""
    client = _client_with_types(schema_rows=[], counts={})

    with patch.object(ghe, "log") as mock_log, \
         patch.object(grace_metrics, "graph_node_count") as gnode, \
         patch.object(grace_metrics, "graph_edge_count") as gedge, \
         patch.object(grace_metrics, "graph_orphan_node_count") as gorph, \
         patch.object(grace_metrics, "graph_density") as gdens, \
         patch.object(
             grace_metrics, "graph_exporter_last_success_seconds"
         ) as glast:
        await ghe._publish_one_tick(client, topn=20)
        await ghe._publish_one_tick(client, topn=20)
        await ghe._publish_one_tick(client, topn=20)

    # Single INFO for the whole no-schema stretch (state flag), zero errors.
    info_calls = [
        c for c in mock_log.info.call_args_list
        if c.args and c.args[0] == "graph_health_exporter.schema_not_yet_synced"
    ]
    assert len(info_calls) == 1
    mock_log.error.assert_not_called()
    mock_log.warning.assert_not_called()
    # Nothing published while the schema is empty.
    gnode.set.assert_not_called()
    gedge.set.assert_not_called()
    gorph.set.assert_not_called()
    gdens.set.assert_not_called()
    glast.set.assert_not_called()


@pytest.mark.asyncio
async def test_schema_flag_resets_when_types_appear():
    """Types appearing after an empty stretch re-arm the one-shot INFO."""
    empty = _client_with_types(schema_rows=[], counts={})
    populated = _client_with_types(
        schema_rows=[{"name": "Company", "type": "vertex"}],
        counts={"Company": 1},
        orphans={"Company": 0},
    )

    with patch.object(ghe, "log") as mock_log:
        assert await ghe._collect_health_snapshot(empty) is None
        assert ghe._schema_not_synced_logged is True
        report = await ghe._collect_health_snapshot(populated)
        assert report is not None
        assert ghe._schema_not_synced_logged is False
        # Empty again → logs once more (re-armed).
        assert await ghe._collect_health_snapshot(empty) is None

    info_calls = [
        c for c in mock_log.info.call_args_list
        if c.args and c.args[0] == "graph_health_exporter.schema_not_yet_synced"
    ]
    assert len(info_calls) == 2
