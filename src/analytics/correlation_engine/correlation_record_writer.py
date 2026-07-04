"""Atomic writer for ``correlation_runs`` + ``diagnostic_records`` (Chunk 33,
D248).

Single transaction: insert the run row, bulk-insert all diagnostic
records, commit. On unique-constraint collision (replay of the same
``(run_id, pattern_name, ontology_module)``) raises
``DiagnosticRecordIdempotencyError`` so the orchestrator can degrade to
``status='partial_failure'`` and log a warning instead of failing hard.

The writer is **sync** — the orchestrator calls it from
``asyncio.to_thread()`` to bridge the async/sync boundary (matches
Chunk 32's ``signal_record_writer`` pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import IntegrityError

from src.analytics.correlation_engine.database import (
    correlation_runs,
    diagnostic_records,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from src.analytics.correlation_engine.base import (
        CorrelationRun,
        DiagnosticRecord,
    )

log = structlog.get_logger()


class DiagnosticRecordIdempotencyError(Exception):
    """Raised when a diagnostic-record write violates the
    ``(run_id, pattern_name, ontology_module)`` unique constraint."""


def _record_row(record: "DiagnosticRecord") -> dict:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "pattern_name": record.pattern_name,
        "ontology_module": record.ontology_module,
        "suspected_root_cause_module": record.suspected_root_cause_module,
        "correlation_strength": record.correlation_strength,
        "contributing_signals": record.contributing_signals,
        "evidence_snapshot": record.evidence_snapshot,
        "human_summary": record.human_summary,
        "detected_at": record.detected_at,
    }


def _run_row(run: "CorrelationRun") -> dict:
    return {
        "id": run.id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "config_hash": run.config_hash,
    }


def write_run(
    records: "list[DiagnosticRecord]",
    run: "CorrelationRun",
    session_factory: "sessionmaker",
) -> None:
    """Write the ``correlation_runs`` row and any ``diagnostic_records`` rows.

    Single transaction. On ``IntegrityError`` (unique constraint on
    ``(run_id, pattern_name, ontology_module)``), raises
    ``DiagnosticRecordIdempotencyError``.
    """
    session = session_factory()
    try:
        try:
            session.execute(correlation_runs.insert().values(**_run_row(run)))
            if records:
                session.execute(
                    diagnostic_records.insert(),
                    [_record_row(r) for r in records],
                )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            log.warning(
                "correlation_record_writer.idempotency_violation",
                run_id=str(run.id),
                error=str(exc),
            )
            raise DiagnosticRecordIdempotencyError(
                f"Run {run.id} already written"
            ) from exc
    finally:
        session.close()
