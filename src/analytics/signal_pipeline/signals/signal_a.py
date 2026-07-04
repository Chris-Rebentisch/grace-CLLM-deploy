"""Signal A — Missing types (D241/D245).

Detects a rising rate of extraction-time INSUFFICIENT verdicts per
``ontology_module``. The PromQL must include the ``verdict="INSUFFICIENT"``
label selector — the upstream histogram (D173 ``grace_extraction_triple_confidence``)
carries both ``ontology_module`` and ``verdict`` labels; querying without
the verdict filter aggregates all verdicts and does not operationalize
"missing types".

Threshold (D245): fires on **rising** rate. Strength is computed as

    strength = clamp(
        (current - baseline * sigma) / (baseline * sigma),
        0, 1,
    )

when ``current > baseline * sigma``, else ``0.0``. ``sigma`` is the
configurable ``sigma_multiplier`` (default 3.0).

Cross-type collision sub-mode (F-0041 / ISS-0034): the ER fix flags
same-normalized-name different-type vertex pairs at mint time
(``resolution_note`` containing ``cross_type_name_collision`` in
``entity_resolution_log``), but no operator surface read those rows — the
validation-run duplicate ("Crestline Water Authority" as BOTH Legal_Entity
conf 0.3 AND Vendor conf 0.8) was only found by a human reviewer. When
``signal_a.cross_type_collision_enabled`` is true, recent flagged log
rows ALSO fire under ``ontology_module="__global__"`` (the log carries no
module column) with strength
``min(1.0, distinct_collisions / cross_type_count_threshold)`` and
evidence marked ``mode: "cross_type_collision"``. Trend behavior is
unchanged; when both modes hit the same module they merge into one record
(unique constraint ``uq_analytics_signals_run_signal_module``, F-0037
merge discipline) with the collision evidence nested under
``cross_type_collision``. Substrate choice: (a) Postgres
``entity_resolution_log`` — Signal A already opens Postgres sessions for
evidence; (b) an ArcadeDB graph scan for PRE-FIX collisions is the
documented backfill gap (this pipeline reads Prometheus + Postgres only;
see ISS-0034 addendum).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import structlog
from sqlalchemy import text

from src.analytics import metrics as grace_metrics
from src.analytics._prometheus_query_helpers import query_with_coldstart_hint
from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRunContext,
    note_prerequisites_not_met,
)

log = structlog.get_logger(__name__)

# Mandatory: see D241 / FAIL-gate #13. Tests assert this string is in
# the PromQL we send to Prometheus.
VERDICT_SELECTOR = 'verdict="INSUFFICIENT"'
_BASELINE_MIN_SAMPLES = 100


def _build_promql(window: str) -> str:
    """Build the PromQL fragment used by both windows."""
    return (
        f'sum by (ontology_module) ('
        f'rate(grace_extraction_triple_confidence_count{{{VERDICT_SELECTOR}}}[{window}])'
        f')'
    )


def _to_window(days: int) -> str:
    return f"{days}d"


def _safe_strength(current: float, baseline: float, sigma: float) -> float:
    threshold = baseline * sigma
    if threshold <= 0.0 or current <= threshold:
        return 0.0
    return max(0.0, min(1.0, (current - threshold) / threshold))


class SignalADetector(SignalDetector):
    signal_type: ClassVar[str] = "A"

    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        cfg = run_context.config.signal_a
        if not cfg.enabled:
            return []

        current_window = _to_window(cfg.current_window_days)
        baseline_window = _to_window(cfg.baseline_window_days)

        current_promql = _build_promql(current_window)
        baseline_promql = _build_promql(baseline_window)

        current = await run_context.prometheus_reader.query_instant(current_promql)
        baseline = await run_context.prometheus_reader.query_instant(baseline_promql)
        baseline = query_with_coldstart_hint(baseline_promql, baseline)

        records: list[SignalRecord] = []
        now = datetime.now(UTC)

        # Prerequisites visibility (C1 follow-up): with no Prometheus history
        # for the current window the TREND mode emits nothing — make that
        # no-op explicit so it is distinguishable from "ran, found nothing".
        # F-0041 / ISS-0034: this used to `return []`; it no longer short-
        # circuits the whole detector, because the cross-type collision
        # sub-mode below reads Postgres, not Prometheus — collision
        # visibility must not depend on Prometheus history. Trend logic and
        # the prerequisites diagnostics are unchanged.
        if not current.entries:
            missing = ["prometheus_current_window_data"]
            if not baseline.entries:
                missing.append("prometheus_baseline")
            log.warning(
                "signal_detector_prerequisites_not_met",
                detector="A",
                missing=missing,
                promql=current_promql,
            )
            note_prerequisites_not_met(run_context, "A", missing)
        elif not baseline.entries:
            log.info(
                "signal_detector_baseline_absent",
                detector="A",
                missing="prometheus_baseline",
                promql=baseline_promql,
            )

        baseline_by_module = {
            entry.metric.get("ontology_module", "__global__"): entry.value
            for entry in baseline.entries
        }

        for entry in current.entries:
            module = entry.metric.get("ontology_module", "__global__")
            if (
                run_context.target_ontology_modules
                and module not in run_context.target_ontology_modules
            ):
                continue

            current_rate = entry.value
            baseline_rate = baseline_by_module.get(module, 0.0)

            insufficient_samples = baseline_rate < (
                _BASELINE_MIN_SAMPLES
                / max(1, cfg.baseline_window_days * 86_400)
            )

            evidence = await self._fetch_evidence(
                run_context=run_context, ontology_module=module, window_days=cfg.current_window_days,
            )

            if insufficient_samples:
                evidence["note"] = "insufficient samples"
                strength = 0.0
            else:
                strength = _safe_strength(
                    current_rate, baseline_rate, cfg.sigma_multiplier
                )

            evidence.update(
                {
                    "current_rate_per_sec": current_rate,
                    "baseline_rate_per_sec": baseline_rate,
                    "sigma_multiplier": cfg.sigma_multiplier,
                }
            )

            grace_metrics.signal_a_strength.set(
                strength, attributes={"ontology_module": module}
            )

            records.append(
                SignalRecord(
                    run_id=run_context.run_id,
                    signal_type="A",
                    ontology_module=module,
                    strength=strength,
                    evidence_snapshot=evidence,
                    detected_at=now,
                )
            )

        # F-0041 / ISS-0034 — cross-type collision sub-mode runs ALONGSIDE
        # the trend mode. Merged per module (never a second row per module)
        # so the uq_analytics_signals_run_signal_module constraint holds —
        # same merge discipline as the F-0037 point-in-time modes in
        # signal_d.py / signal_f.py.
        if cfg.cross_type_collision_enabled:
            by_module = {r.ontology_module: r for r in records}
            collision_records = await self._cross_type_collision_records(
                run_context, now
            )
            for module, col in collision_records.items():
                existing = by_module.get(module)
                if existing is None:
                    by_module[module] = col
                else:
                    # Trend evidence keys stay at top level (unchanged for
                    # existing consumers); collision evidence nests
                    # additively under its own key (F-0037 pattern).
                    merged_evidence = dict(existing.evidence_snapshot)
                    merged_evidence["cross_type_collision"] = (
                        col.evidence_snapshot
                    )
                    by_module[module] = existing.model_copy(
                        update={
                            "strength": max(existing.strength, col.strength),
                            "evidence_snapshot": merged_evidence,
                        }
                    )
                grace_metrics.signal_a_strength.set(
                    by_module[module].strength,
                    attributes={"ontology_module": module},
                )
            return list(by_module.values())

        return records

    async def _cross_type_collision_records(
        self, run_context: SignalRunContext, now: datetime
    ) -> dict[str, SignalRecord]:
        """Cross-type duplicate detection (F-0041 / ISS-0034).

        capture-the-why (F-0041 / ISS-0034, signal-surface follow-up): the
        extraction resolver now FLAGS same-normalized-name different-type
        collisions at mint time (``entity_resolution_log`` rows with
        ``resolution_note`` containing ``cross_type_name_collision``), but
        those rows had no operator surface — the signal plane is the
        designed surface for graph smells. This sub-mode aggregates recent
        flagged rows into at most one Signal A record under
        ``"__global__"`` (the log carries no ontology_module column).

        Documented backfill gap: collisions minted BEFORE the ISS-0034
        resolver fix, or via write paths that bypass the resolver, never
        produced a log row and are invisible here; catching those needs an
        ArcadeDB graph scan, which Signal A does not perform (the pipeline
        reads Prometheus + Postgres only). Recorded in the ISS-0034
        addendum and in the emitted evidence ``limitation`` field.
        """
        cfg = run_context.config.signal_a
        module = "__global__"
        if (
            run_context.target_ontology_modules
            and module not in run_context.target_ontology_modules
        ):
            return {}

        cutoff = now - timedelta(days=max(1, cfg.cross_type_window_days))
        rows = await self._collision_log_rows(run_context, cutoff)

        # Aggregate by normalized colliding name — N log rows for the same
        # name are ONE collision (distinct-name count drives strength).
        by_name: dict[str, dict] = {}
        for extracted_name, extracted_type, candidates_json in rows:
            key = (extracted_name or "").strip().lower()
            if not key:
                continue
            agg = by_name.setdefault(
                key,
                {
                    "name": extracted_name,
                    "types": set(),
                    "grace_ids": set(),
                    "log_rows": 0,
                },
            )
            agg["log_rows"] += 1
            if extracted_type:
                agg["types"].add(extracted_type)
            for cand in candidates_json or []:
                if (
                    isinstance(cand, dict)
                    and cand.get("flag") == "cross_type_name_collision"
                ):
                    if cand.get("entity_type"):
                        agg["types"].add(cand["entity_type"])
                    if cand.get("grace_id"):
                        agg["grace_ids"].add(str(cand["grace_id"]))

        if not by_name:
            return {}

        threshold = max(1, cfg.cross_type_count_threshold)
        distinct = len(by_name)
        # D245 — strength normalized to [0, 1].
        strength = max(0.0, min(1.0, distinct / threshold))
        collisions = sorted(
            (
                {
                    "name": agg["name"],
                    "types": sorted(agg["types"]),
                    "grace_ids": sorted(agg["grace_ids"]),
                    "log_rows": agg["log_rows"],
                }
                for agg in by_name.values()
            ),
            key=lambda c: (c["name"] or "").lower(),
        )
        evidence = {
            # Marker distinguishing this from trend evidence (F-0041).
            "mode": "cross_type_collision",
            "distinct_collisions": distinct,
            "count_threshold": threshold,
            "window_days": cfg.cross_type_window_days,
            # Cap evidence size; distinct_collisions carries the full count.
            "collisions": collisions[:20],
            "limitation": (
                "reads entity_resolution_log only — cross-type duplicates "
                "minted before the ISS-0034 resolver fix, or via paths that "
                "bypass the resolver, produced no log row and are not "
                "visible; an ArcadeDB graph scan is the documented backfill "
                "gap (ISS-0034 addendum)."
            ),
        }
        return {
            module: SignalRecord(
                run_id=run_context.run_id,
                signal_type="A",
                ontology_module=module,
                strength=strength,
                evidence_snapshot=evidence,
                detected_at=now,
            )
        }

    async def _collision_log_rows(
        self, run_context: SignalRunContext, cutoff: datetime
    ) -> list[tuple[str | None, str | None, list | None]]:
        """Recent ``entity_resolution_log`` rows flagged by the ISS-0034 ER
        fix (``resolution_note`` containing ``cross_type_name_collision``;
        the resolver semicolon-joins notes, hence LIKE not equality).
        Failures are log-and-continue — the sub-mode must never break the
        trend mode (F-0041 / ISS-0034)."""
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT extracted_name, extracted_type, candidates_json
                    FROM entity_resolution_log
                    WHERE resolved_at >= :cutoff
                      AND resolution_note LIKE :note_marker
                    """
                ),
                {
                    "cutoff": cutoff,
                    "note_marker": "%cross_type_name_collision%",
                },
            ).all()
            return [(r[0], r[1], r[2]) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_a.collision_log_rows_failed", error=str(exc))
            return []
        finally:
            session.close()

    async def _fetch_evidence(
        self,
        *,
        run_context: SignalRunContext,
        ontology_module: str,
        window_days: int,
    ) -> dict:
        """Top-5 entity-type counts from extraction_claims for the current window."""
        cutoff = datetime.now(UTC) - timedelta(days=max(1, window_days))
        session = run_context.session_factory()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT entity_type, COUNT(*) AS n
                    FROM extraction_claims
                    WHERE ontology_module = :module
                      AND created_at >= :cutoff
                      AND entity_type IS NOT NULL
                    GROUP BY entity_type
                    ORDER BY n DESC
                    LIMIT 5
                    """
                ),
                {"module": ontology_module, "cutoff": cutoff},
            ).all()
            top5 = [{"entity_type": r[0], "count": int(r[1])} for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_a.evidence_fetch_failed", error=str(exc))
            top5 = []
        finally:
            session.close()
        return {"top_entity_types": top5}
