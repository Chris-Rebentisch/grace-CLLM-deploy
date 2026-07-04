"""Tests for the ``extraction_quality_problem`` detector (D250)."""

from __future__ import annotations

import json

import httpx
import pytest

from src.analytics.correlation_engine.config import CorrelationEngineConfig
from src.analytics.correlation_engine.patterns.extraction_quality_problem import (
    ExtractionQualityProblemDetector,
)

from tests.analytics.correlation_engine.conftest import make_prom_vector


def _prom_handler_factory(values: dict[str, float]):
    """Return a handler that maps query substring to scalar value."""

    def handler(request: httpx.Request) -> httpx.Response:
        promql = request.url.params.get("query", "")
        for needle, value in values.items():
            if needle in promql:
                return httpx.Response(
                    200, json=make_prom_vector([{"metric": {}, "value": value}])
                )
        # Fallback: empty vector.
        return httpx.Response(200, json=make_prom_vector([]))

    return handler


@pytest.mark.asyncio
async def test_extraction_quality_problem_happy_path_emits_global_record(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Signal A high + retrieval rate dropped > sigma×std → emits one record."""
    insert_synthetic_signal_run(
        [
            ("A", "finance", 0.8),
            ("A", "legal", 0.6),
        ]
    )
    # current << baseline; baseline_std small so the drop crosses 3σ.
    handler = _prom_handler_factory(
        {
            f'rate(grace_retrieval_strategy_contributions_total[1d])': 1.0,
            f'rate(grace_retrieval_strategy_contributions_total[14d])': 10.0,
            "stddev_over_time": 0.5,
        }
    )
    ctx = correlation_run_context(prom_handler=handler)

    records = await ExtractionQualityProblemDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.pattern_name == "extraction_quality_problem"
    assert rec.ontology_module == "__global__"
    assert rec.suspected_root_cause_module == "extraction"
    assert 0.0 < rec.correlation_strength <= 1.0
    assert any(c.get("signal") == "A" for c in rec.contributing_signals)
    assert "signal_a_value" in rec.evidence_snapshot
    assert len(rec.human_summary) <= 240


@pytest.mark.asyncio
async def test_extraction_quality_problem_low_signals_emits_nothing(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """All-low signal A and no retrieval drop → no record emitted; global-only."""
    insert_synthetic_signal_run(
        [
            ("A", "finance", 0.1),
            ("A", "legal", 0.05),
        ]
    )
    handler = _prom_handler_factory(
        {
            f'rate(grace_retrieval_strategy_contributions_total[1d])': 5.0,
            f'rate(grace_retrieval_strategy_contributions_total[14d])': 5.0,
            "stddev_over_time": 0.1,
        }
    )
    ctx = correlation_run_context(prom_handler=handler)

    records = await ExtractionQualityProblemDetector().detect(ctx)

    # No record. Even if Signal A had been per-module high, the detector
    # only ever emits __global__ (D250).
    assert records == []
