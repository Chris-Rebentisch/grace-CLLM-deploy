"""CP2 model + DB-constraint tests for the correlation record writer."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.analytics.correlation_engine.base import (
    CorrelationRun,
    DiagnosticRecord,
)
from src.analytics.correlation_engine.correlation_record_writer import (
    DiagnosticRecordIdempotencyError,
    write_run,
)


def test_diagnostic_record_strength_clamped_to_unit_interval():
    """DiagnosticRecord rejects correlation_strength outside [0, 1]."""
    with pytest.raises(ValidationError):
        DiagnosticRecord(
            run_id=uuid4(),
            pattern_name="extraction_quality_problem",
            ontology_module="finance",
            suspected_root_cause_module="extraction",
            correlation_strength=1.5,
            contributing_signals=[{"signal": "C", "strength": 0.8}],
            evidence_snapshot={"note": "out-of-range"},
            human_summary="bad",
            detected_at=datetime.now(UTC),
        )

    rec = DiagnosticRecord(
        run_id=uuid4(),
        pattern_name="extraction_quality_problem",
        ontology_module="finance",
        suspected_root_cause_module="extraction",
        correlation_strength=0.5,
        contributing_signals=[{"signal": "C", "strength": 0.8}],
        evidence_snapshot={"note": "ok"},
        human_summary="ok",
        detected_at=datetime.now(UTC),
    )
    assert 0.0 <= rec.correlation_strength <= 1.0


def test_correlation_run_validates_status_and_triggered_by():
    """CorrelationRun.status must be in the literal set; triggered_by is 'cli'."""
    CorrelationRun(
        id=uuid4(),
        started_at=datetime.now(UTC),
        completed_at=None,
        status="running",
        triggered_by="cli",
        config_hash="abc123",
    )

    with pytest.raises(ValidationError):
        CorrelationRun(
            id=uuid4(),
            started_at=datetime.now(UTC),
            completed_at=None,
            status="weird-status",  # type: ignore[arg-type]
            triggered_by="cli",
            config_hash="abc123",
        )


def test_unique_constraint_round_trip_raises_idempotency_error(
    test_session_factory, cleanup_correlation_tables
):
    """Re-writing the same run row raises DiagnosticRecordIdempotencyError."""
    run_id = uuid4()
    started = datetime.now(UTC)
    run = CorrelationRun(
        id=run_id,
        started_at=started,
        completed_at=started,
        status="success",
        triggered_by="cli",
        config_hash="hash1",
    )
    rec = DiagnosticRecord(
        run_id=run_id,
        pattern_name="extraction_quality_problem",
        ontology_module="finance",
        suspected_root_cause_module="extraction",
        correlation_strength=0.42,
        contributing_signals=[{"signal": "C", "strength": 0.8}],
        evidence_snapshot={"note": "first"},
        human_summary="quality regression observed",
        detected_at=started,
    )
    write_run([rec], run, test_session_factory)

    # Second call with the same run_id violates PK on correlation_runs first;
    # writer surfaces it as DiagnosticRecordIdempotencyError.
    with pytest.raises(DiagnosticRecordIdempotencyError):
        write_run([rec], run, test_session_factory)


def test_write_run_with_empty_records_persists_only_run_row(
    test_session_factory, cleanup_correlation_tables, test_engine
):
    """Empty records list still writes the correlation_runs row."""
    from sqlalchemy import text

    run_id = uuid4()
    started = datetime.now(UTC)
    run = CorrelationRun(
        id=run_id,
        started_at=started,
        completed_at=started,
        status="success",
        triggered_by="cli",
        config_hash="hash-empty",
    )
    write_run([], run, test_session_factory)

    with test_engine.connect() as conn:
        n_runs = conn.execute(
            text("SELECT count(*) FROM correlation_runs WHERE id = :id"),
            {"id": str(run_id)},
        ).scalar()
        n_records = conn.execute(
            text("SELECT count(*) FROM diagnostic_records WHERE run_id = :id"),
            {"id": str(run_id)},
        ).scalar()
    assert n_runs == 1
    assert n_records == 0
