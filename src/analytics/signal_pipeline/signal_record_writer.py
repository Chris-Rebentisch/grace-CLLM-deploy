"""Atomic writer for ``signal_runs`` + ``analytics_signals`` (D240).

Single transaction: insert the run row, bulk-insert all records, commit.
On unique-constraint collision (replay of the same run_id) raises
``SignalRecordIdempotencyError`` so the orchestrator can degrade to
``status='partial_failure'`` and log a warning instead of failing hard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import MetaData, Table
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from src.analytics.signal_pipeline.base import SignalRecord, SignalRun

log = structlog.get_logger()


class SignalRecordIdempotencyError(Exception):
    """Raised when a signal-record write violates the run/signal_type/module
    unique constraint (i.e. a replay of an already-written run)."""


def _record_row(record: "SignalRecord") -> dict:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "signal_type": record.signal_type,
        "ontology_module": record.ontology_module,
        "strength": record.strength,
        "evidence_snapshot": record.evidence_snapshot,
        "detected_at": record.detected_at,
    }


def _run_row(run: "SignalRun") -> dict:
    return {
        "id": run.id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "config_hash": run.config_hash,
    }


def write_run(
    records: "list[SignalRecord]",
    run: "SignalRun",
    session_factory: "sessionmaker",
) -> None:
    """Write the ``signal_runs`` row and any ``analytics_signals`` rows.

    Single transaction. On ``IntegrityError`` (unique constraint on
    ``(run_id, signal_type, ontology_module)``), raise
    ``SignalRecordIdempotencyError``.
    """
    metadata = MetaData()
    # Reflect via Core ``Table`` definitions to avoid pulling models that
    # might not yet exist; columns we need are explicit.
    from sqlalchemy import Column, DateTime, Float, Text
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

    signal_runs = Table(
        "signal_runs",
        metadata,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("status", Text, nullable=False),
        Column("triggered_by", Text, nullable=False),
        Column("config_hash", Text, nullable=False),
    )
    analytics_signals = Table(
        "analytics_signals",
        metadata,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("run_id", PG_UUID(as_uuid=True), nullable=False),
        Column("signal_type", Text, nullable=False),
        Column("ontology_module", Text, nullable=False),
        Column("strength", Float, nullable=False),
        Column("evidence_snapshot", JSONB, nullable=False),
        Column("detected_at", DateTime(timezone=True), nullable=False),
    )

    session = session_factory()
    try:
        try:
            session.execute(signal_runs.insert().values(**_run_row(run)))
            if records:
                session.execute(
                    analytics_signals.insert(),
                    [_record_row(r) for r in records],
                )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            log.warning(
                "signal_record_writer.idempotency_violation",
                run_id=str(run.id),
                error=str(exc),
            )
            raise SignalRecordIdempotencyError(
                f"Run {run.id} already written"
            ) from exc
    finally:
        session.close()
