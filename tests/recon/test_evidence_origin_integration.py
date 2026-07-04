"""Chunk 59, CP7 — evidence_origin integration tests for recon reports.

Six tests covering:
1. SourceTypeBreakdown model construction + serialization.
2. Gap report populates source_type_breakdown from ArcadeDB origin query.
3. Gap report graceful degradation when origin query fails.
4. DivergenceMapEntry source_origins populated from ArcadeDB.
5. Documented Reality aggregation with evidence_origin='document' filter.
6. Documented Reality aggregation with evidence_origin='both' (no filter).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.recon_models import (
    DivergenceMapEntry,
    SourceTypeBreakdown,
)


# ---------------------------------------------------------------------------
# 1. SourceTypeBreakdown model construction + serialization.
# ---------------------------------------------------------------------------


def test_source_type_breakdown_defaults() -> None:
    """SourceTypeBreakdown defaults to zeros; round-trips via model_dump."""
    b = SourceTypeBreakdown()
    assert b.document == 0
    assert b.communication == 0
    assert b.mixed == 0
    d = b.model_dump()
    assert d == {"document": 0, "communication": 0, "mixed": 0}


def test_source_type_breakdown_values() -> None:
    b = SourceTypeBreakdown(document=10, communication=5, mixed=2)
    assert b.document == 10
    assert b.communication == 5
    assert b.mixed == 2


# ---------------------------------------------------------------------------
# 2. Gap report populates source_type_breakdown from ArcadeDB origin query.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_report_source_type_breakdown() -> None:
    """compute_gap_report returns a source_type_breakdown when ArcadeDB
    origin query succeeds."""
    from src.ontology.recon_gap_report import compute_gap_report

    session_id = uuid4()

    # Mock DB session.
    mock_session = MagicMock()
    # _read_decisions returns empty list.
    mock_session.execute.return_value.fetchall.return_value = []
    mock_session.execute.return_value.fetchone.return_value = MagicMock(
        reviewer="testuser"
    )

    # Mock ArcadeDB client.
    mock_arcade = AsyncMock()

    # First call: _read_graph_counts SQL → type counts above floor.
    # Second call: origin breakdown SQL.
    call_count = 0

    async def _mock_execute_sql(sql, **kwargs):
        nonlocal call_count
        call_count += 1
        if "GROUP BY @type" in sql and "COALESCE(evidence_origin" not in sql:
            # _read_graph_counts
            return {
                "result": [
                    {"type_name": "Company", "cnt": 80},
                    {"type_name": "Person", "cnt": 30},
                ]
            }
        return {"result": []}

    mock_arcade.execute_sql = AsyncMock(side_effect=_mock_execute_sql)

    # Origin query goes through arcade_client.query() not execute_sql.
    mock_arcade.query = AsyncMock(
        return_value={
            "result": [
                {"origin": "document", "cnt": 80},
                {"origin": "communication", "cnt": 25},
                {"origin": "hybrid", "cnt": 5},
            ]
        }
    )

    result = await compute_gap_report(
        session_id=session_id,
        db_session=mock_session,
        arcade_client=mock_arcade,
        graph_population_floor=50,
    )

    assert result.source_type_breakdown is not None
    assert result.source_type_breakdown.document == 80
    assert result.source_type_breakdown.communication == 25
    assert result.source_type_breakdown.mixed == 5


# ---------------------------------------------------------------------------
# 3. Gap report graceful degradation when origin query fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_report_source_breakdown_degradation() -> None:
    """When the origin query fails, source_type_breakdown defaults
    document=total_v, communication=0, mixed=0."""
    from src.ontology.recon_gap_report import compute_gap_report

    session_id = uuid4()
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []
    mock_session.execute.return_value.fetchone.return_value = MagicMock(
        reviewer="testuser"
    )

    mock_arcade = AsyncMock()

    async def _mock_execute_sql(sql, **kwargs):
        if "GROUP BY @type" in sql:
            return {
                "result": [
                    {"type_name": "Company", "cnt": 120},
                ]
            }
        return {"result": []}

    mock_arcade.execute_sql = AsyncMock(side_effect=_mock_execute_sql)
    # Origin query raises.
    mock_arcade.query = AsyncMock(side_effect=RuntimeError("origin query failed"))

    result = await compute_gap_report(
        session_id=session_id,
        db_session=mock_session,
        arcade_client=mock_arcade,
        graph_population_floor=50,
    )

    assert result.source_type_breakdown is not None
    # Degradation: total_v goes to document.
    assert result.source_type_breakdown.document == 120
    assert result.source_type_breakdown.communication == 0
    assert result.source_type_breakdown.mixed == 0


# ---------------------------------------------------------------------------
# 4. DivergenceMapEntry source_origins populated.
# ---------------------------------------------------------------------------


def test_divergence_map_entry_source_origins() -> None:
    """DivergenceMapEntry accepts and serializes source_origins."""
    entry = DivergenceMapEntry(
        element_name="Company",
        element_type="entity_type",
        instance_count=10,
        source_origins=["document", "communication"],
    )
    assert entry.source_origins == ["document", "communication"]
    d = entry.model_dump()
    assert d["source_origins"] == ["document", "communication"]


def test_divergence_map_entry_source_origins_default() -> None:
    """DivergenceMapEntry defaults source_origins to empty list (backward compat)."""
    entry = DivergenceMapEntry(
        element_name="Person",
        element_type="entity_type",
        instance_count=5,
    )
    assert entry.source_origins == []


# ---------------------------------------------------------------------------
# 5. Documented Reality aggregation with evidence_origin filter.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documented_reality_origin_filter_document() -> None:
    """evidence_origin='document' scopes queries with WHERE COALESCE."""
    from src.analytics.documented_reality import (
        compute_documented_reality_aggregations,
    )

    mock_arcade = AsyncMock()
    captured_sqls: list[str] = []

    async def _capture_sql(sql, **kwargs):
        captured_sqls.append(sql)
        # First call enumerates types via `schema:types`; later calls are the
        # per-type count queries that carry the evidence_origin filter.
        if "schema:types" in sql:
            return {"result": [
                {"name": "Legal_Entity", "type": "v"},
                {"name": "party_to", "type": "e"},
            ]}
        return {"result": [{"cnt": 42}]}

    mock_arcade.execute_sql = AsyncMock(side_effect=_capture_sql)

    await compute_documented_reality_aggregations(
        mock_arcade, evidence_origin="document"
    )

    # The per-type count queries carry the WHERE clause; the schema-enumeration
    # query does not (and should not).
    count_sqls = [s for s in captured_sqls if "count(*)" in s.lower()]
    assert count_sqls
    for sql in count_sqls:
        assert "COALESCE(evidence_origin, 'document') = 'document'" in sql


# ---------------------------------------------------------------------------
# 6. Documented Reality aggregation with evidence_origin='both' (no filter).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documented_reality_origin_filter_both() -> None:
    """evidence_origin='both' should NOT add WHERE clause."""
    from src.analytics.documented_reality import (
        compute_documented_reality_aggregations,
    )

    mock_arcade = AsyncMock()
    captured_sqls: list[str] = []

    async def _capture_sql(sql, **kwargs):
        captured_sqls.append(sql)
        if "schema:types" in sql:
            return {"result": [
                {"name": "Legal_Entity", "type": "v"},
                {"name": "party_to", "type": "e"},
            ]}
        return {"result": [{"cnt": 10}]}

    mock_arcade.execute_sql = AsyncMock(side_effect=_capture_sql)

    await compute_documented_reality_aggregations(
        mock_arcade, evidence_origin="both"
    )

    # No count query carries an evidence_origin filter when origin is 'both'.
    count_sqls = [s for s in captured_sqls if "count(*)" in s.lower()]
    assert count_sqls
    for sql in count_sqls:
        assert "evidence_origin" not in sql
