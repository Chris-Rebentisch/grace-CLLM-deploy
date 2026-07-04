"""Tests for the ``graph_or_index_problem`` detector (D250)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.correlation_engine.patterns.graph_or_index_problem import (
    GraphOrIndexProblemDetector,
)

from tests.analytics.correlation_engine.conftest import make_prom_vector


def _handler_factory(values: dict[str, float]):
    def handler(request: httpx.Request) -> httpx.Response:
        promql = request.url.params.get("query", "")
        for needle, value in values.items():
            if needle in promql:
                return httpx.Response(
                    200, json=make_prom_vector([{"metric": {}, "value": value}])
                )
        return httpx.Response(200, json=make_prom_vector([]))

    return handler


@pytest.mark.asyncio
async def test_graph_or_index_problem_happy_path(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Latency p95 spike + all six signals quiet → emits one __global__ record."""
    insert_synthetic_signal_run(
        [
            ("A", "finance", 0.05),
            ("B", "finance", 0.05),
            ("C", "finance", 0.05),
            ("D", "finance", 0.05),
            ("E", "finance", 0.05),
            ("F", "finance", 0.05),
        ]
    )
    # current p95 = 5s, baseline = 0.5s, std = 0.1s → 5 > 0.5 + 3×0.1.
    handler = _handler_factory(
        {
            "stddev_over_time": 0.1,
            "avg_over_time": 0.5,
            # Bare current p95 query (matches when neither stddev nor avg
            # appear in the promql).
            "histogram_quantile": 5.0,
        }
    )
    # Reorder: stddev_over_time and avg_over_time both wrap histogram_quantile,
    # so we need the more-specific match to win. Use a custom handler.

    def precise_handler(request: httpx.Request) -> httpx.Response:
        promql = request.url.params.get("query", "")
        if "stddev_over_time" in promql:
            value = 0.1
        elif "avg_over_time" in promql:
            value = 0.5
        elif "histogram_quantile" in promql:
            value = 5.0
        else:
            return httpx.Response(200, json=make_prom_vector([]))
        return httpx.Response(
            200, json=make_prom_vector([{"metric": {}, "value": value}])
        )

    ctx = correlation_run_context(prom_handler=precise_handler)
    records = await GraphOrIndexProblemDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.pattern_name == "graph_or_index_problem"
    assert rec.ontology_module == "__global__"
    assert rec.suspected_root_cause_module == "graph"
    assert 0.0 < rec.correlation_strength <= 1.0
    assert "current_p95_seconds" in rec.evidence_snapshot
    assert rec.evidence_snapshot["signal_maxes"]["A"] < 0.3
