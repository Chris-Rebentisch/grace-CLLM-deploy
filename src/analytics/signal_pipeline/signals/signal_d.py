"""Signal D — Deprecation (D245).

Mann-Kendall test (``pymannkendall.original_test``, alpha 0.05) over
daily extraction-claim counts per ``entity_type``. Fires when the trend
is **decreasing** with ``p < 0.05``. Strength = ``clamp(1 - p, 0, 1)``.

NaN values in the daily count series are replaced with 0 before the test
(R10). Series shorter than ``mann_kendall_min_points`` skip emission
(R4).

Point-in-time mode (F-0037 / ISS-0028): the trend test needs >= 10 days
of history, so a single deprecated-type claim could never fire Signal D
— point-in-time deprecated use was invisible to the signal plane (only
the validator quarantine caught it, constraint rule
``deprecated_entity_type``, F-17). When
``signal_d.point_in_time_enabled`` is true, recent quarantined claims
carrying that violation ALSO fire with strength
``min(1.0, count / point_in_time_count_threshold)`` and evidence marked
``mode: "point_in_time"``. Trend behavior is unchanged; when both modes
hit the same module they merge into one record (unique constraint
``uq_analytics_signals_run_signal_module``) with the point-in-time
evidence nested under ``point_in_time``.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
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

log = structlog.get_logger(__name__)


def _replace_nan(values: list[float]) -> list[float]:
    return [0.0 if (v is None or math.isnan(v)) else float(v) for v in values]


class SignalDDetector(SignalDetector):
    signal_type: ClassVar[str] = "D"

    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        cfg = run_context.config.signal_d
        if not cfg.enabled:
            return []

        min_points = run_context.config.mann_kendall_min_points
        alpha = run_context.config.mann_kendall_alpha
        cutoff = datetime.now(UTC) - timedelta(days=max(min_points, cfg.baseline_window_days))

        rows = await self._daily_counts(run_context, cutoff)

        # Group by (module, entity_type) -> ordered list of (day, count).
        grouped: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
        for module, entity_type, day, count in rows:
            grouped.setdefault((module, entity_type), []).append((day, int(count)))

        records: list[SignalRecord] = []
        now = datetime.now(UTC)
        seen_modules: set[str] = set()
        # Track best (highest) strength per module to surface as the gauge value.
        per_module_best: dict[str, dict] = {}

        for (module, entity_type), series in grouped.items():
            if (
                run_context.target_ontology_modules
                and module not in run_context.target_ontology_modules
            ):
                continue
            series.sort(key=lambda p: p[0])
            counts = _replace_nan([s[1] for s in series])
            if len(counts) < min_points:
                continue
            try:
                result = mk.original_test(counts, alpha=alpha)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "signal_d.mk_failed",
                    module=module,
                    entity_type=entity_type,
                    error=str(exc),
                )
                continue
            if result.trend != "decreasing" or result.p >= alpha:
                continue
            strength = max(0.0, min(1.0, 1.0 - float(result.p)))
            evidence = {
                "entity_type": entity_type,
                "daily_counts": counts,
                "trend": result.trend,
                "p_value": float(result.p),
            }
            best = per_module_best.get(module)
            if best is None or strength > best["strength"]:
                per_module_best[module] = {
                    "strength": strength,
                    "evidence": evidence,
                }

            records.append(
                SignalRecord(
                    run_id=run_context.run_id,
                    signal_type="D",
                    ontology_module=module,
                    strength=strength,
                    evidence_snapshot=evidence,
                    detected_at=now,
                )
            )
            seen_modules.add(module)

        # Emit one gauge value per module (the highest strength seen).
        for module, best in per_module_best.items():
            grace_metrics.signal_d_strength.set(
                best["strength"], attributes={"ontology_module": module}
            )

        # Note: per-(module, entity_type) records can produce multiple rows
        # per (run, signal, module), violating the unique constraint. The
        # spec mandates one record per (run, signal, ontology_module);
        # collapse to the best evidence per module.
        collapsed: dict[str, SignalRecord] = {}
        for r in records:
            existing = collapsed.get(r.ontology_module)
            if existing is None or r.strength > existing.strength:
                collapsed[r.ontology_module] = r

        # F-0037 / ISS-0028 — point-in-time mode runs ALONGSIDE the trend
        # mode. Merged per module (never a second row per module) so the
        # uq_analytics_signals_run_signal_module constraint holds.
        if cfg.point_in_time_enabled:
            pit_records = await self._point_in_time_records(run_context, now)
            for module, pit in pit_records.items():
                existing = collapsed.get(module)
                if existing is None:
                    collapsed[module] = pit
                else:
                    # Trend evidence keys stay at top level (unchanged for
                    # existing consumers, e.g. signal_mapping reads
                    # evidence["entity_type"] via .get()); point-in-time
                    # evidence nests additively.
                    merged_evidence = dict(existing.evidence_snapshot)
                    merged_evidence["point_in_time"] = pit.evidence_snapshot
                    collapsed[module] = existing.model_copy(
                        update={
                            "strength": max(existing.strength, pit.strength),
                            "evidence_snapshot": merged_evidence,
                        }
                    )
                grace_metrics.signal_d_strength.set(
                    collapsed[module].strength,
                    attributes={"ontology_module": module},
                )

        return list(collapsed.values())

    async def _point_in_time_records(
        self, run_context: SignalRunContext, now: datetime
    ) -> dict[str, SignalRecord]:
        """Point-in-time deprecated-type detection (F-0037 / ISS-0028).

        Reuses the extraction_claims substrate the trend mode already reads
        (no new store dependency): the constraint validator (F-17, rule 15)
        quarantines claims that instantiate a deprecated schema type with a
        ``deprecated_entity_type`` violation, so recent quarantined claims
        carrying that rule ARE the point-in-time deprecated-use population.
        Returns at most one record per ontology_module (best entity_type by
        count as headline; all types listed in evidence).
        """
        cfg = run_context.config.signal_d
        cutoff = now - timedelta(days=cfg.point_in_time_window_days)
        rows = await self._deprecated_type_counts(run_context, cutoff)

        threshold = max(1, cfg.point_in_time_count_threshold)
        per_module: dict[str, list[tuple[str, int]]] = {}
        for module, entity_type, count in rows:
            module = module or "__global__"
            if (
                run_context.target_ontology_modules
                and module not in run_context.target_ontology_modules
            ):
                continue
            per_module.setdefault(module, []).append((entity_type, int(count)))

        records: dict[str, SignalRecord] = {}
        for module, type_counts in per_module.items():
            type_counts.sort(key=lambda tc: tc[1], reverse=True)
            top_type, top_count = type_counts[0]
            # D245 — strength normalized to [0, 1].
            strength = max(0.0, min(1.0, top_count / threshold))
            evidence = {
                # Marker distinguishing this from trend evidence (F-0037).
                "mode": "point_in_time",
                "entity_type": top_type,
                "count": top_count,
                "window_days": cfg.point_in_time_window_days,
                "count_threshold": threshold,
                "deprecated_types": [
                    {"entity_type": et, "count": n} for et, n in type_counts
                ],
            }
            records[module] = SignalRecord(
                run_id=run_context.run_id,
                signal_type="D",
                ontology_module=module,
                strength=strength,
                evidence_snapshot=evidence,
                detected_at=now,
            )
        return records

    async def _deprecated_type_counts(
        self, run_context: SignalRunContext, cutoff: datetime
    ) -> list[tuple[str | None, str, int]]:
        """Count recent quarantined claims per (module, entity_type) whose
        constraint_violations include rule ``deprecated_entity_type``
        (F-0037 / ISS-0028; validator rule 15, F-17)."""
        marker = json.dumps([{"rule": "deprecated_entity_type"}])
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT ontology_module, entity_type, COUNT(*) AS n
                    FROM extraction_claims
                    WHERE created_at >= :cutoff
                      AND status = 'quarantined'
                      AND entity_type IS NOT NULL
                      AND constraint_violations @> CAST(:marker AS jsonb)
                    GROUP BY ontology_module, entity_type
                    """
                ),
                {"cutoff": cutoff, "marker": marker},
            ).all()
            return [(r[0], r[1], r[2]) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_d.deprecated_type_counts_failed", error=str(exc))
            return []
        finally:
            session.close()

    async def _daily_counts(
        self, run_context: SignalRunContext, cutoff: datetime
    ) -> list[tuple[str, str, datetime, int]]:
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT
                        ontology_module,
                        entity_type,
                        date_trunc('day', created_at) AS day,
                        COUNT(*) AS n
                    FROM extraction_claims
                    WHERE created_at >= :cutoff
                      AND ontology_module IS NOT NULL
                      AND entity_type IS NOT NULL
                    GROUP BY ontology_module, entity_type, day
                    ORDER BY ontology_module, entity_type, day
                    """
                ),
                {"cutoff": cutoff},
            ).all()
            return [(r[0], r[1], r[2], r[3]) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_d.daily_counts_failed", error=str(exc))
            return []
        finally:
            session.close()
