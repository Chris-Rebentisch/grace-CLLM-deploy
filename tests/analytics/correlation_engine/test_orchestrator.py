"""Orchestrator tests (CP5, D248).

Three tests:
1. Happy path: all detectors succeed → ``status='success'``, run row + records persisted.
2. Per-detector failure isolation → ``status='partial_failure'``.
3. All detectors fail → ``status='error'`` (no exception propagates, run row
   persisted with empty records).
"""

from __future__ import annotations

from typing import ClassVar
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.analytics.correlation_engine.base import (
    CorrelationDetector,
    CorrelationRunContext,
    DiagnosticRecord,
    PatternNameLiteral,
    RootCauseModuleLiteral,
)
from src.analytics.correlation_engine.orchestrator import run_all


class _AlwaysEmits(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "extraction_quality_problem"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "extraction"

    async def detect(self, run_context: CorrelationRunContext) -> list[DiagnosticRecord]:
        return [
            DiagnosticRecord(
                run_id=run_context.run_id,
                pattern_name=self.pattern_name,
                ontology_module="__global__",
                suspected_root_cause_module=self.suspected_root_cause_module,
                correlation_strength=0.7,
                contributing_signals=[{"signal": "A", "strength": 0.7}],
                evidence_snapshot={"note": "synthetic"},
                human_summary="synthetic record",
                detected_at=datetime.now(UTC),
            )
        ]


class _AlwaysEmpty(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "graph_or_index_problem"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "graph"

    async def detect(self, run_context: CorrelationRunContext) -> list[DiagnosticRecord]:
        return []


class _AlwaysRaises(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "schema_drift_per_module"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "ontology"

    async def detect(self, run_context: CorrelationRunContext) -> list[DiagnosticRecord]:
        raise RuntimeError("synthetic detector failure")


@pytest.mark.asyncio
async def test_run_all_happy_path_writes_records(
    correlation_run_context,
    test_engine,
    test_session_factory,
    cleanup_correlation_tables,
):
    """All detectors succeed → status='success' and rows are persisted."""
    ctx = correlation_run_context()
    run, records = await run_all(
        [_AlwaysEmits(), _AlwaysEmpty()],
        context=ctx,
        session_factory=test_session_factory,
    )
    assert run.status == "success"
    assert len(records) == 1
    assert run.config_hash and len(run.config_hash) == 16

    with test_engine.connect() as conn:
        n_runs = conn.execute(
            text("SELECT count(*) FROM correlation_runs WHERE id = :id"),
            {"id": str(run.id)},
        ).scalar()
        n_records = conn.execute(
            text("SELECT count(*) FROM diagnostic_records WHERE run_id = :id"),
            {"id": str(run.id)},
        ).scalar()
    assert n_runs == 1
    assert n_records == 1


@pytest.mark.asyncio
async def test_run_all_isolates_detector_failure(
    correlation_run_context,
    test_session_factory,
    cleanup_correlation_tables,
):
    """One detector raises → status='partial_failure' and other records still land."""
    ctx = correlation_run_context()
    run, records = await run_all(
        [_AlwaysEmits(), _AlwaysRaises()],
        context=ctx,
        session_factory=test_session_factory,
    )
    assert run.status == "partial_failure"
    assert len(records) == 1


@pytest.mark.asyncio
async def test_run_all_all_failed_status_error(
    correlation_run_context,
    test_session_factory,
    cleanup_correlation_tables,
):
    """Every detector raises → status='error', run row persisted with no records."""
    ctx = correlation_run_context()
    run, records = await run_all(
        [_AlwaysRaises()],
        context=ctx,
        session_factory=test_session_factory,
    )
    assert run.status == "error"
    assert records == []
