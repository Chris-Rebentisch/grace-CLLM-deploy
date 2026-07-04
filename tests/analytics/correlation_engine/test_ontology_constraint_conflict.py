"""Tests for the ``ontology_constraint_conflict`` detector (D535)."""

from __future__ import annotations

import pytest

from src.analytics.correlation_engine.patterns.ontology_constraint_conflict import (
    OntologyConstraintConflictDetector,
)


@pytest.mark.asyncio
async def test_ontology_constraint_conflict_happy_path(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Per-module Signal E and B both ≥ 0.5 → one record per module (recall),
    and modules missing one of the two signals stay quiet (precision)."""
    insert_synthetic_signal_run(
        [
            ("E", "finance", 0.8),
            ("B", "finance", 0.6),  # both above → fires
            ("E", "legal", 0.7),
            ("B", "legal", 0.4),  # B below threshold → no record
            ("E", "healthcare", 0.55),
            ("B", "healthcare", 0.55),  # both above → fires
            ("E", "ops", 0.9),  # E only, no B → no record
        ]
    )
    ctx = correlation_run_context()
    records = await OntologyConstraintConflictDetector().detect(ctx)

    modules = sorted(r.ontology_module for r in records)
    assert modules == ["finance", "healthcare"]
    for rec in records:
        assert rec.pattern_name == "ontology_constraint_conflict"
        assert rec.suspected_root_cause_module == "ontology"
        assert 0.0 < rec.correlation_strength <= 1.0
        signals = {c.get("signal") for c in rec.contributing_signals}
        assert {"E", "B"}.issubset(signals)
        assert "ontology_module" in rec.evidence_snapshot
        # D535 refinement: the boundary is contestable -> both candidates surfaced.
        assert rec.evidence_snapshot["candidate_root_causes"] == ["ontology", "extraction"]
        assert rec.evidence_snapshot["boundary_case"] is True
        assert len(rec.human_summary) <= 240


@pytest.mark.asyncio
async def test_ontology_constraint_conflict_quiet_without_conjunction(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """A controlled latest run with E present but B absent (and unrelated
    signals) → no records. Hermetic precision check: the detector reads the
    latest successful signal_run, so inserting a fresh run shadows any
    pre-existing global state (D252)."""
    insert_synthetic_signal_run(
        [
            ("E", "finance", 0.9),  # E only — no B in finance
            ("C", "finance", 0.8),
            ("A", "legal", 0.7),  # unrelated signals
            ("F", "legal", 0.9),
        ]
    )
    ctx = correlation_run_context()
    records = await OntologyConstraintConflictDetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_ontology_constraint_conflict_strength_is_mean(
    correlation_run_context,
    insert_synthetic_signal_run,
    cleanup_correlation_tables,
):
    """Strength is the mean of the two signal strengths."""
    insert_synthetic_signal_run([("E", "finance", 0.8), ("B", "finance", 0.6)])
    ctx = correlation_run_context()
    records = await OntologyConstraintConflictDetector().detect(ctx)
    assert len(records) == 1
    assert records[0].correlation_strength == pytest.approx(0.7)
