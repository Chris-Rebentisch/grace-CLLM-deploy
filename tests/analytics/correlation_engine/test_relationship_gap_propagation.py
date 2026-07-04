"""Tests for the ``relationship_gap_propagation`` detector (D250)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.correlation_engine.patterns.relationship_gap_propagation import (
    RelationshipGapPropagationDetector,
)

from tests.analytics.correlation_engine.conftest import make_prom_vector


@pytest.mark.asyncio
async def test_relationship_gap_propagation_happy_path(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Signal B per-module high + global zero-results spike → record per module."""
    insert_synthetic_signal_run(
        [
            ("B", "finance", 0.8),
            ("B", "legal", 0.2),  # below threshold
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        promql = request.url.params.get("query", "")
        if "stddev_over_time" in promql:
            value = 0.05
        elif "[14d]" in promql:
            value = 0.5
        elif "[1d]" in promql:
            value = 5.0
        else:
            return httpx.Response(200, json=make_prom_vector([]))
        return httpx.Response(
            200, json=make_prom_vector([{"metric": {}, "value": value}])
        )

    ctx = correlation_run_context(prom_handler=handler)
    records = await RelationshipGapPropagationDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.pattern_name == "relationship_gap_propagation"
    assert rec.ontology_module == "finance"
    assert rec.suspected_root_cause_module == "extraction"
    assert 0.0 < rec.correlation_strength <= 1.0
    assert any(c.get("signal") == "B" for c in rec.contributing_signals)
    assert rec.evidence_snapshot["increase"] > 0.0
    assert len(rec.human_summary) <= 240
