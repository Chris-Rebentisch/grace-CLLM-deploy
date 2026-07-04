"""Tests for the ``schema_drift_per_module`` detector (D250)."""

from __future__ import annotations

import pytest

from src.analytics.correlation_engine.patterns.schema_drift_per_module import (
    SchemaDriftPerModuleDetector,
)


@pytest.mark.asyncio
async def test_schema_drift_per_module_happy_path(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Per-module Signal C and D both ≥ 0.5 → emits one record per module."""
    insert_synthetic_signal_run(
        [
            ("C", "finance", 0.7),
            ("D", "finance", 0.6),
            ("C", "legal", 0.4),  # below threshold — no record
            ("D", "legal", 0.7),
            ("C", "healthcare", 0.55),  # both above
            ("D", "healthcare", 0.55),
        ]
    )
    ctx = correlation_run_context()
    records = await SchemaDriftPerModuleDetector().detect(ctx)

    modules = sorted(r.ontology_module for r in records)
    assert modules == ["finance", "healthcare"]
    for rec in records:
        assert rec.pattern_name == "schema_drift_per_module"
        assert rec.suspected_root_cause_module == "ontology"
        assert 0.0 < rec.correlation_strength <= 1.0
        signals = {c.get("signal") for c in rec.contributing_signals}
        assert {"C", "D"}.issubset(signals)
        assert "ontology_module" in rec.evidence_snapshot
        assert len(rec.human_summary) <= 240
