"""Signal pipeline orchestrator (D240, D247).

Concurrently runs every signal detector against a single
``SignalRunContext`` and persists the result to ``signal_runs`` /
``analytics_signals``. Per-detector failures are isolated: one detector
raising does not poison sibling detectors.

Status semantics:
- ``success`` — every detector returned (possibly empty) records.
- ``partial_failure`` — one or more detectors raised; remaining ones
  produced records.
- ``error`` — every detector raised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.analytics.signal_pipeline.base import (
    RunStatusLiteral,
    SignalDetector,
    SignalRecord,
    SignalRun,
    SignalRunContext,
)
from src.analytics.signal_pipeline.config import SignalPipelineConfig
from src.analytics.prometheus_reader import PrometheusReader
from src.analytics.signal_pipeline.signal_record_writer import (
    SignalRecordIdempotencyError,
    write_run,
)

logger = logging.getLogger(__name__)


def _config_hash(config: SignalPipelineConfig) -> str:
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


async def run_all(
    detectors: list[SignalDetector],
    *,
    context: SignalRunContext,
    session_factory,
    dry_run: bool = False,
) -> tuple[SignalRun, list[SignalRecord]]:
    """Run every detector concurrently and persist the result.

    Args:
        detectors: ordered list of detector instances.
        context: per-run carrier (immutable). Detectors share it.
        session_factory: SQLAlchemy ``sessionmaker`` used by ``write_run``.
        dry_run: when True, skip persistence and return collected records.
    """
    started_at = context.started_at
    run_id = context.run_id

    coros = [d.detect(context) for d in detectors]
    results = await asyncio.gather(*coros, return_exceptions=True)

    records: list[SignalRecord] = []
    succeeded = 0
    failed = 0
    for detector, outcome in zip(detectors, results, strict=True):
        if isinstance(outcome, BaseException):
            failed += 1
            logger.error(
                "signal_detector_failed",
                extra={
                    "signal_type": detector.signal_type,
                    "error": repr(outcome),
                },
            )
            continue
        succeeded += 1
        records.extend(outcome)

    status = _resolve_status(
        total=len(detectors), succeeded=succeeded, failed=failed
    )
    run = SignalRun(
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
        write_run(records=records, run=run, session_factory=session_factory)
    except SignalRecordIdempotencyError:
        # Idempotent re-run: same (run_id, signal_type, ontology_module).
        # Accept silently — caller can inspect status / logs.
        logger.warning(
            "signal_run_idempotent_skip",
            extra={"run_id": str(run_id)},
        )
    return run, records


def make_default_context(
    *,
    config: SignalPipelineConfig,
    session_factory,
    target_ontology_modules: list[str] | None = None,
    run_id: UUID | None = None,
) -> SignalRunContext:
    """Build a SignalRunContext using the configured Prometheus URL."""
    reader = PrometheusReader(base_url=config.prometheus_url)
    return SignalRunContext(
        run_id=run_id or uuid4(),
        started_at=datetime.now(UTC),
        prometheus_reader=reader,
        session_factory=session_factory,
        config=config,
        target_ontology_modules=target_ontology_modules,
    )
