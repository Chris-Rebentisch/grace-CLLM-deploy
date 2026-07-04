"""Correlation engine orchestrator (Chunk 33, D248).

Concurrently runs every ``CorrelationDetector`` against a single
``CorrelationRunContext`` and persists the result to ``correlation_runs``
+ ``diagnostic_records``. Per-detector failures are isolated: one
detector raising does not poison sibling detectors.

Status semantics (matches signal_pipeline):
- ``success`` — every detector returned (possibly empty) records.
- ``partial_failure`` — one or more detectors raised; others succeeded.
- ``error`` — every detector raised.

The writer in ``correlation_record_writer`` is **sync**; the orchestrator
calls it through ``asyncio.to_thread()`` (D248).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.analytics.correlation_engine.base import (
    CorrelationDetector,
    CorrelationRun,
    CorrelationRunContext,
    DiagnosticRecord,
    RunStatusLiteral,
)
from src.analytics.correlation_engine.config import CorrelationEngineConfig
from src.analytics.correlation_engine.correlation_record_writer import (
    DiagnosticRecordIdempotencyError,
    write_run,
)
from src.analytics.correlation_engine.patterns import DEFAULT_DETECTOR_CLASSES
from src.analytics.prometheus_reader import PrometheusReader

logger = logging.getLogger(__name__)


def _config_hash(config: CorrelationEngineConfig) -> str:
    """Deterministic short hash of the config payload."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _resolve_status(
    *, total: int, succeeded: int, failed: int
) -> RunStatusLiteral:
    if failed == 0:
        return "success"
    if succeeded == 0:
        return "error"
    return "partial_failure"


def default_detectors() -> list[CorrelationDetector]:
    """Instantiate the five locked-by-D250 detectors."""
    return [cls() for cls in DEFAULT_DETECTOR_CLASSES]


async def run_all(
    detectors: list[CorrelationDetector] | None = None,
    *,
    context: CorrelationRunContext,
    session_factory,
    dry_run: bool = False,
) -> tuple[CorrelationRun, list[DiagnosticRecord]]:
    """Run every detector concurrently and persist the run + records.

    Args:
        detectors: ordered list of detector instances. ``None`` =
            ``default_detectors()``.
        context: per-run carrier (immutable). Detectors share it.
        session_factory: SQLAlchemy ``sessionmaker`` used by ``write_run``.
        dry_run: when True, skip persistence and return collected records.
    """
    if detectors is None:
        detectors = default_detectors()

    started_at = context.started_at
    run_id = context.run_id

    coros = [d.detect(context) for d in detectors]
    results = await asyncio.gather(*coros, return_exceptions=True)

    records: list[DiagnosticRecord] = []
    succeeded = 0
    failed = 0
    for detector, outcome in zip(detectors, results, strict=True):
        if isinstance(outcome, BaseException):
            failed += 1
            logger.error(
                "correlation_detector_failed",
                extra={
                    "pattern_name": detector.pattern_name,
                    "error": repr(outcome),
                },
            )
            continue
        succeeded += 1
        records.extend(outcome)

    status = _resolve_status(
        total=len(detectors), succeeded=succeeded, failed=failed
    )
    run = CorrelationRun(
        id=run_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        status=status,
        triggered_by="cli",
        config_hash=_config_hash(context.config),
    )

    if dry_run:
        return run, records

    try:
        await asyncio.to_thread(
            write_run,
            records,
            run,
            session_factory,
        )
    except DiagnosticRecordIdempotencyError:
        logger.warning(
            "correlation_run_idempotent_skip",
            extra={"run_id": str(run_id)},
        )

    return run, records


def make_default_context(
    *,
    config: CorrelationEngineConfig,
    session_factory,
    target_ontology_modules: list[str] | None = None,
    run_id: UUID | None = None,
) -> CorrelationRunContext:
    """Build a CorrelationRunContext using the configured Prometheus URL."""
    reader = PrometheusReader(base_url=config.prometheus_url)
    return CorrelationRunContext(
        run_id=run_id or uuid4(),
        started_at=datetime.now(UTC),
        prometheus_reader=reader,
        session_factory=session_factory,
        config=config,
        target_ontology_modules=target_ontology_modules,
    )
