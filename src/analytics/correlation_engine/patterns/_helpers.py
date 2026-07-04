"""Shared helpers for correlation pattern detectors (D250/D252).

Detectors read upstream telemetry from three sources only:
- ``analytics_signals`` rows (latest per signal_type/module within a
  lookback window across successful ``signal_runs`` — F-0038/ISS-0027)
- per-signal strength gauges in Prometheus
- the curated raw-Prometheus allowlist (D252)

These helpers centralize the analytics_signals read so that every
detector goes through the same query path; D252 / FAIL-gate #8 forbids
raw reads of ``extraction_claims`` / ``cq_test_runs``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from src.analytics.correlation_engine.base import CorrelationRunContext


def fetch_latest_signal_strengths(
    run_context: CorrelationRunContext,
    signal_type: str,
) -> dict[str, dict[str, Any]]:
    """Return ``{ontology_module: {strength, evidence_snapshot, signal_run_id, detected_at}}``.

    F-0038 / ISS-0027 (validation run 2026-07-03): this helper used to
    join only the single most-recent ``signal_runs`` row. Per-signal
    invocations (``run-all --signal X``) persist each signal type in its own
    run row, so "latest run only" saw at most one signal type and silently
    blanked every conjunction pattern (first correlation attempt found 0
    patterns despite all six signals persisted). Fix: window instead of
    latest-run-only — for each ontology_module, take the most recently
    detected signal of ``signal_type`` across ALL successful runs within
    ``config.signal_lookback_hours`` (default 24h).

    Evidence honesty (F-0038): each per-module entry carries the
    ``signal_run_id`` it came from, so diagnostic evidence can attribute
    contributing signals to their originating run across the window.

    Returns an empty mapping when no in-window signal exists.
    """
    cfg = run_context.config
    lookback_hours = float(getattr(cfg, "signal_lookback_hours", 24.0))
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

    session = run_context.session_factory()
    try:
        rows = session.execute(
            text(
                """
                SELECT s.ontology_module, s.strength, s.evidence_snapshot,
                       s.run_id, s.detected_at
                FROM analytics_signals s
                JOIN signal_runs r ON s.run_id = r.id
                WHERE s.signal_type = :signal_type
                  AND r.status = 'success'
                  AND s.detected_at >= :cutoff
                ORDER BY s.detected_at DESC
                """
            ),
            {"signal_type": signal_type, "cutoff": cutoff},
        ).all()
    finally:
        session.close()

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        module = row[0]
        detected_at = row[4]
        # F-0038/ISS-0027 belt-and-braces: re-apply the window cutoff in
        # Python (guards against naive/aware drift and non-SQL sessions in
        # unit tests) and keep only the newest row per module even if the
        # backing rows arrive unordered.
        if detected_at is not None:
            if detected_at.tzinfo is None:
                detected_at = detected_at.replace(tzinfo=UTC)
            if detected_at < cutoff:
                continue
        existing = latest.get(module)
        if existing is not None:
            existing_dt = existing.get("detected_at")
            if (
                existing_dt is not None
                and detected_at is not None
                and detected_at <= existing_dt
            ):
                continue
            if existing_dt is not None and detected_at is None:
                continue
        latest[module] = {
            "strength": float(row[1]),
            "evidence_snapshot": dict(row[2] or {}),
            # UUID → str so downstream contributing_signals /
            # evidence_snapshot stay JSON-serializable (base.py contract).
            "signal_run_id": str(row[3]) if row[3] is not None else None,
            "detected_at": detected_at,
        }
    return latest
