"""Tests for the ``cq_regression_pre_extraction`` detector (D250, D251)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.correlation_engine.patterns.cq_regression_pre_extraction import (
    CQRegressionPreExtractionDetector,
)

from tests.analytics.correlation_engine.conftest import make_prom_matrix


@pytest.mark.asyncio
async def test_cq_regression_pre_extraction_happy_path(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Signal F ≥ 0.5 + stable extraction throughput → emits one record."""
    insert_synthetic_signal_run(
        [
            ("F", "finance", 0.75),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Stable throughput: 14 daily samples all near 1.0 — Mann-Kendall
        # returns 'no trend'.
        values = [(float(1700000000 + i * 86400), 1.0 + (i % 2) * 0.01) for i in range(14)]
        return httpx.Response(
            200,
            json=make_prom_matrix([{"metric": {"ontology_module": "finance"}, "values": values}]),
        )

    ctx = correlation_run_context(prom_handler=handler)
    records = await CQRegressionPreExtractionDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.pattern_name == "cq_regression_pre_extraction"
    assert rec.ontology_module == "finance"
    assert rec.suspected_root_cause_module == "discovery"
    assert rec.correlation_strength == pytest.approx(0.75, abs=1e-9)
    assert rec.evidence_snapshot["throughput_trend"] != "decreasing"
    assert any(c.get("signal") == "F" for c in rec.contributing_signals)
    assert len(rec.human_summary) <= 240
