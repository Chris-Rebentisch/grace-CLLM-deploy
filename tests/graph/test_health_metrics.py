"""Tests for graph health metrics (mocked ArcadeDB, no live server).

F-0003 / ISS-0043: ``get_health_report`` no longer queries the generic
``V`` / ``E`` supertypes (which do not exist on this deployment's
databases) — it enumerates concrete types from ``schema:types`` and
aggregates per-type. These tests mock ``execute_sql`` with a
query-inspecting responder instead of a positional side_effect list.
"""

from __future__ import annotations

import re

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.health_metrics import get_health_report

_TYPE_RE = re.compile(r"FROM `([^`]+)`")


def _mock_client(
    schema_rows: list[dict],
    counts: dict[str, int] | None = None,
    orphans: dict[str, int] | None = None,
    deprecated: dict[str, int] | None = None,
) -> ArcadeClient:
    """ArcadeClient whose execute_sql answers per-concrete-type probes.

    ``schema_rows`` feeds the ``schema:types`` enumeration; ``counts`` /
    ``orphans`` / ``deprecated`` map type name → cnt for the three probe
    shapes. Every executed SQL string is recorded on ``client.queries``.
    """
    counts = counts or {}
    orphans = orphans or {}
    deprecated = deprecated or {}

    client = ArcadeClient(config=ArcadeConfig())
    client.queries = []  # type: ignore[attr-defined]

    async def fake_execute_sql(sql: str, *args, **kwargs):
        client.queries.append(sql)
        if "schema:types" in sql:
            return {"result": schema_rows}
        m = _TYPE_RE.search(sql)
        assert m, f"unexpected non-typed query: {sql}"
        name = m.group(1)
        if "bothE" in sql:
            return {"result": [{"cnt": orphans.get(name, 0)}]}
        if "_deprecated = true" in sql:
            return {"result": [{"cnt": deprecated.get(name, 0)}]}
        return {"result": [{"cnt": counts.get(name, 0)}]}

    client.execute_sql = fake_execute_sql  # type: ignore[method-assign]
    return client


_SCHEMA = [
    {"name": "Person", "type": "vertex"},
    {"name": "Company", "type": "vertex"},
    {"name": "WORKS_AT", "type": "edge"},
    {"name": "KNOWS", "type": "edge"},
]


@pytest.mark.asyncio
async def test_typed_db_aggregates_per_concrete_type():
    """Typed DB: totals/distributions aggregate from per-type counts."""
    client = _mock_client(
        _SCHEMA,
        counts={"Person": 60, "Company": 40, "WORKS_AT": 120, "KNOWS": 80},
        orphans={"Person": 3, "Company": 1},
        deprecated={"Person": 5, "WORKS_AT": 2},
    )
    report = await get_health_report(client)
    assert report.total_vertices == 100
    assert report.total_edges == 200
    assert {tc.type_name: tc.count for tc in report.vertex_types} == {
        "Person": 60,
        "Company": 40,
    }
    assert {tc.type_name: tc.count for tc in report.edge_types} == {
        "WORKS_AT": 120,
        "KNOWS": 80,
    }
    assert report.orphan_count == 4
    assert report.orphan_rate == pytest.approx(0.04)
    assert report.deprecated_vertices == 5
    assert report.deprecated_edges == 2


@pytest.mark.asyncio
async def test_never_queries_v_or_e_supertypes():
    """F-0003 / ISS-0043: no query touches the generic V/E supertypes."""
    client = _mock_client(
        _SCHEMA,
        counts={"Person": 60, "Company": 40, "WORKS_AT": 120, "KNOWS": 80},
    )
    await get_health_report(client)
    for sql in client.queries:  # type: ignore[attr-defined]
        assert "FROM V" not in sql
        assert "FROM E" not in sql


@pytest.mark.asyncio
async def test_density_and_avg_edges_calculation():
    """Density = total_edges / total_vertices."""
    client = _mock_client(
        _SCHEMA,
        counts={"Person": 50, "WORKS_AT": 150},
    )
    report = await get_health_report(client)
    assert report.density == pytest.approx(3.0)
    assert report.avg_edges_per_vertex == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_vless_schemaless_db_returns_clean_empty_report():
    """F-0003 / ISS-0043: a database with no vertex types (the V-less /
    schema-not-synced case that used to 500) returns a well-formed empty
    report — and never issues a FROM V query."""
    client = _mock_client(schema_rows=[])
    report = await get_health_report(client)
    assert report.total_vertices == 0
    assert report.total_edges == 0
    assert report.density == 0.0
    assert report.orphan_count == 0
    assert report.orphan_rate == 0.0
    assert report.vertex_types == []
    assert report.edge_types == []
    assert report.deprecated_vertices == 0
    assert report.deprecated_edges == 0
    # Only the schema:types enumeration ran — nothing else was probed.
    assert client.queries == ["SELECT name, type FROM schema:types"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_edge_only_schema_returns_clean_empty_report():
    """No vertex types (edges only) also short-circuits to the empty report."""
    client = _mock_client(schema_rows=[{"name": "KNOWS", "type": "edge"}])
    report = await get_health_report(client)
    assert report.total_vertices == 0
    assert report.total_edges == 0
    assert len(client.queries) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_empty_graph_with_types_returns_zeros():
    """Types registered but zero rows: all-zero report, zero-count types
    dropped from the distributions, and no orphan/deprecated probes issued
    for empty types."""
    client = _mock_client(_SCHEMA)  # all counts default to 0
    report = await get_health_report(client)
    assert report.total_vertices == 0
    assert report.total_edges == 0
    assert report.density == 0.0
    assert report.avg_edges_per_vertex == 0.0
    assert report.orphan_count == 0
    assert report.orphan_rate == 0.0
    assert report.vertex_types == []
    assert report.edge_types == []
    # schema:types + one count(*) per type; no bothE/deprecated probes.
    assert not any("bothE" in q for q in client.queries)  # type: ignore[attr-defined]
    assert not any("_deprecated" in q for q in client.queries)  # type: ignore[attr-defined]
