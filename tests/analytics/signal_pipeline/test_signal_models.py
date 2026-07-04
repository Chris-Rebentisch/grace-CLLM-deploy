"""CP2 model + DB-constraint tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.analytics.signal_pipeline.base import SignalRecord, SignalRun
from src.analytics.signal_pipeline.signal_record_writer import (
    SignalRecordIdempotencyError,
    write_run,
)


def test_signal_record_strength_clamped_to_unit_interval():
    """SignalRecord rejects strength outside [0, 1]."""
    with pytest.raises(ValidationError):
        SignalRecord(
            run_id=uuid4(),
            signal_type="A",
            ontology_module="finance",
            strength=1.5,
            evidence_snapshot={"note": "out-of-range"},
            detected_at=datetime.now(UTC),
        )

    rec = SignalRecord(
        run_id=uuid4(),
        signal_type="A",
        ontology_module="finance",
        strength=0.5,
        evidence_snapshot={"note": "ok"},
        detected_at=datetime.now(UTC),
    )
    assert 0.0 <= rec.strength <= 1.0


def test_signal_run_validates_status_and_triggered_by():
    """SignalRun status must be in the literal set; triggered_by is 'cli'."""
    SignalRun(
        id=uuid4(),
        started_at=datetime.now(UTC),
        completed_at=None,
        status="running",
        triggered_by="cli",
        config_hash="abc123",
    )

    with pytest.raises(ValidationError):
        SignalRun(
            id=uuid4(),
            started_at=datetime.now(UTC),
            completed_at=None,
            status="weird-status",  # type: ignore[arg-type]
            triggered_by="cli",
            config_hash="abc123",
        )


def test_unique_constraint_round_trip_raises_idempotency_error(
    test_session_factory, cleanup_signal_tables
):
    """Re-writing the same (run_id, signal_type, ontology_module) raises."""
    run_id = uuid4()
    started = datetime.now(UTC)
    run = SignalRun(
        id=run_id,
        started_at=started,
        completed_at=started,
        status="success",
        triggered_by="cli",
        config_hash="hash1",
    )
    rec = SignalRecord(
        run_id=run_id,
        signal_type="A",
        ontology_module="finance",
        strength=0.42,
        evidence_snapshot={"note": "first"},
        detected_at=started,
    )
    write_run([rec], run, test_session_factory)

    # Second call with the same run_id violates PK on signal_runs first;
    # writer surfaces it as SignalRecordIdempotencyError.
    with pytest.raises(SignalRecordIdempotencyError):
        write_run([rec], run, test_session_factory)
