"""Signal F — CQ-driven Gaps (D243/D245).

CQ-test-failure-spike proxy. Reads ``cq_test_runs`` (CQTestRunRow) and
computes per-run failure rate ``failing / total_cqs`` over the most
recent ``mann_kendall_min_points`` runs. Mann-Kendall test fires on an
**increasing** trend with ``p < 0.05``; strength = ``clamp(1 - p, 0, 1)``.

This is the v1 proxy locked by D243. It does NOT import ChromaDB or
ArcadeDB vector-search. Per-CQ evidence comes from ``results_json``
(JSONB) deserialized via ``CQTestResultEntry``, filtered to
``result == 'fail'``.

Empty CQ-runs window (R5) returns ``[]``.

Point-in-time mode (F-0037 / ISS-0028): the trend test needs >=
``mann_kendall_min_points`` completed runs, so a deployment with one
cq-test run got nothing from the signal plane. When
``signal_f.point_in_time_enabled`` is true, failing CQs in the LATEST
completed run ALSO fire with strength = that run's failure rate and
evidence marked ``mode: "point_in_time"``. Trend behavior is unchanged;
because both modes emit under ontology_module ``__global__``, they merge
into a single record (unique constraint
``uq_analytics_signals_run_signal_module``) with point-in-time evidence
nested under ``point_in_time`` when the trend record also emits.

Known limitation (F-0037): a failing CQ may be a DELIBERATE gap
("this should not be answerable yet") rather than a regression — the
cq_test_runs substrate does not distinguish them. The cheap CQ metadata
that exists (``gap_type`` / ``gap_severity`` per failing entry) is
included in evidence so downstream reviewers can triage.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import ClassVar

import pymannkendall as mk
import structlog
from sqlalchemy import text

from src.analytics import metrics as grace_metrics
from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRunContext,
)
from src.ontology.cq_test_models import CQTestResult, CQTestResultEntry

log = structlog.get_logger(__name__)


def _replace_nan(values: list[float]) -> list[float]:
    return [0.0 if (v is None or math.isnan(v)) else float(v) for v in values]


def _decode_failing_entries(results_json, limit: int) -> list[dict]:
    """Deserialize failing CQ entries from a run's ``results_json``.

    Extracted from the trend path unchanged (F-0037 / ISS-0028 refactor) so
    the point-in-time mode reuses the identical substrate decoding — same
    fields, same fail filter; only the caller-supplied cap differs.
    """
    failing_entries: list[dict] = []
    if not results_json:
        return failing_entries
    try:
        for raw in results_json:
            entry = CQTestResultEntry(**raw)
            if entry.result != CQTestResult.FAIL:
                continue
            failing_entries.append(
                {
                    "cq_id": entry.cq_id,
                    "cq_text": entry.cq_text,
                    "gap_type": (
                        entry.gap_type.value if entry.gap_type is not None else None
                    ),
                    "gap_severity": (
                        entry.gap_severity.value
                        if entry.gap_severity is not None
                        else None
                    ),
                }
            )
            if len(failing_entries) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_f.results_decode_failed", error=str(exc))
    return failing_entries


class SignalFDetector(SignalDetector):
    signal_type: ClassVar[str] = "F"

    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        cfg = run_context.config.signal_f
        if not cfg.enabled:
            return []

        min_points = run_context.config.mann_kendall_min_points
        alpha = run_context.config.mann_kendall_alpha

        rows = await self._fetch_runs(run_context, limit=max(min_points * 2, 20))
        if not rows:
            return []

        # rows are ordered ASC by created_at (most recent at end).
        # Compute failure rates only for runs with total_cqs > 0.
        rates: list[float] = []
        last_results_json = None
        last_total = 0
        last_failing = 0
        last_run_id = None
        last_created_at = None
        for row in rows:
            total = int(row[2] or 0)
            failing = int(row[3] or 0)
            if total <= 0:
                continue
            rates.append(failing / total)
            last_results_json = row[4]
            # F-0037 / ISS-0028 — track the latest counted run so the
            # point-in-time mode can fire on it even when the trend window
            # is too short (trend logic below is unchanged).
            last_total = total
            last_failing = failing
            last_run_id = row[0]
            last_created_at = row[1]

        module = "__global__"
        now = datetime.now(UTC)

        # --- Trend mode (D243/D245, unchanged behavior) -------------------
        # F-0037 / ISS-0028 — the short-window early return no longer exits
        # detect() outright; it only skips the TREND record so the
        # point-in-time mode below still gets its chance.
        trend_record: SignalRecord | None = None
        if len(rates) >= min_points:
            trend_rates = _replace_nan(rates)
            result = None
            try:
                result = mk.original_test(trend_rates, alpha=alpha)
            except Exception as exc:  # noqa: BLE001
                log.warning("signal_f.mk_failed", error=str(exc))

            if result is not None:
                if result.trend != "increasing" or result.p >= alpha:
                    strength = 0.0
                else:
                    strength = max(0.0, min(1.0, 1.0 - float(result.p)))

                # Evidence: top-5 failing CQs from the most recent run.
                failing_entries = _decode_failing_entries(last_results_json, limit=5)
                evidence = {
                    "failure_rate_series": trend_rates,
                    "trend": result.trend,
                    "p_value": float(result.p),
                    "top_failing_cqs": failing_entries,
                }
                grace_metrics.signal_f_strength.set(
                    strength, attributes={"ontology_module": module}
                )
                trend_record = SignalRecord(
                    run_id=run_context.run_id,
                    signal_type="F",
                    ontology_module=module,
                    strength=strength,
                    evidence_snapshot=evidence,
                    detected_at=now,
                )

        # --- Point-in-time mode (F-0037 / ISS-0028) ------------------------
        # Any failing CQs in the LATEST completed run fire immediately;
        # strength = that run's failure rate (already in [0, 1] since
        # failing <= total — D245 bounds). Distinguished from trend evidence
        # by the mode marker. Known limitation: nothing in cq_test_runs
        # distinguishes a deliberate-gap CQ from a regression; gap_type /
        # gap_severity metadata is included per entry so reviewers can triage.
        pit_record: SignalRecord | None = None
        if cfg.point_in_time_enabled and last_total > 0 and last_failing > 0:
            pit_strength = max(0.0, min(1.0, last_failing / last_total))
            pit_evidence = {
                "mode": "point_in_time",
                "failure_rate": pit_strength,
                "total_cqs": last_total,
                "failing": last_failing,
                "latest_run_id": str(last_run_id) if last_run_id else None,
                "latest_run_created_at": (
                    last_created_at.isoformat()
                    if hasattr(last_created_at, "isoformat")
                    else last_created_at
                ),
                "failing_cqs": _decode_failing_entries(last_results_json, limit=25),
                "limitation": (
                    "failing CQs may be deliberate gaps rather than "
                    "regressions; substrate carries no deliberate-gap flag "
                    "(see gap_type/gap_severity per entry)"
                ),
            }
            pit_record = SignalRecord(
                run_id=run_context.run_id,
                signal_type="F",
                ontology_module=module,
                strength=pit_strength,
                evidence_snapshot=pit_evidence,
                detected_at=now,
            )

        # --- Merge (one record per (run, signal, module) — the DB unique
        # constraint uq_analytics_signals_run_signal_module forbids emitting
        # both as separate rows under "__global__"; F-0037 / ISS-0028) -----
        if trend_record is not None and pit_record is not None:
            merged_evidence = dict(trend_record.evidence_snapshot)
            merged_evidence["point_in_time"] = pit_record.evidence_snapshot
            merged = trend_record.model_copy(
                update={
                    "strength": max(trend_record.strength, pit_record.strength),
                    "evidence_snapshot": merged_evidence,
                }
            )
            grace_metrics.signal_f_strength.set(
                merged.strength, attributes={"ontology_module": module}
            )
            return [merged]
        if trend_record is not None:
            return [trend_record]
        if pit_record is not None:
            grace_metrics.signal_f_strength.set(
                pit_record.strength, attributes={"ontology_module": module}
            )
            return [pit_record]
        return []

    async def _fetch_runs(
        self, run_context: SignalRunContext, limit: int
    ) -> list[tuple]:
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT id, created_at, total_cqs, failing, results_json
                    FROM cq_test_runs
                    WHERE status = 'completed'
                    ORDER BY created_at ASC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).all()
            return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_f.fetch_runs_failed", error=str(exc))
            return []
        finally:
            session.close()
