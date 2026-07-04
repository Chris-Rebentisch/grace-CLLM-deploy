"""Signal C — Type drift (D241/D242/D245).

Counts ``grace_extraction_validation_failures_total`` for the configured
``kind`` filter (default: ``invalid_entity_type``,
``schema_version_mismatch``) and fires when the current rate exceeds
``baseline_rate * sigma_multiplier``. Signal C intentionally excludes
``invalid_relationship_type`` — that surface lives in Signal E's
domain/range proxy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import structlog

from src.analytics import metrics as grace_metrics
from src.analytics._prometheus_query_helpers import query_with_coldstart_hint
from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRunContext,
    note_prerequisites_not_met,
)

log = structlog.get_logger(__name__)


def _to_window(days: int) -> str:
    return f"{days}d"


def _safe_strength(current: float, baseline: float, sigma: float) -> float:
    threshold = baseline * sigma
    if threshold <= 0.0 or current <= threshold:
        return 0.0
    return max(0.0, min(1.0, (current - threshold) / threshold))


def _build_promql(window: str, kinds: list[str]) -> str:
    kind_re = "|".join(kinds)
    return (
        f'sum by (ontology_module, entity_type) ('
        f'rate(grace_extraction_validation_failures_total{{kind=~"{kind_re}"}}[{window}])'
        f')'
    )


class SignalCDetector(SignalDetector):
    signal_type: ClassVar[str] = "C"

    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        cfg = run_context.config.signal_c
        if not cfg.enabled:
            return []

        kinds = list(cfg.kind_filter)
        current_q = _build_promql(_to_window(cfg.current_window_days), kinds)
        baseline_q = _build_promql(_to_window(cfg.baseline_window_days), kinds)

        current = await run_context.prometheus_reader.query_instant(current_q)
        baseline = await run_context.prometheus_reader.query_instant(baseline_q)
        baseline = query_with_coldstart_hint(baseline_q, baseline)

        # Prerequisites visibility (C1 follow-up): make the empty-history no-op
        # explicit instead of silently returning []. No logic change.
        if not current.entries:
            missing = ["prometheus_current_window_data"]
            if not baseline.entries:
                missing.append("prometheus_baseline")
            log.warning(
                "signal_detector_prerequisites_not_met",
                detector="C",
                missing=missing,
                promql=current_q,
            )
            note_prerequisites_not_met(run_context, "C", missing)
            return []
        if not baseline.entries:
            log.info(
                "signal_detector_baseline_absent",
                detector="C",
                missing="prometheus_baseline",
                promql=baseline_q,
            )

        baseline_idx = {
            (
                e.metric.get("ontology_module", "__global__"),
                e.metric.get("entity_type", "__none__"),
            ): e.value
            for e in baseline.entries
        }

        # Aggregate per ontology_module; pick the most-violating
        # (kind/entity_type) tuples for evidence.
        per_module: dict[str, dict] = {}
        for entry in current.entries:
            module = entry.metric.get("ontology_module", "__global__")
            entity_type = entry.metric.get("entity_type", "__none__")
            if (
                run_context.target_ontology_modules
                and module not in run_context.target_ontology_modules
            ):
                continue
            base = baseline_idx.get((module, entity_type), 0.0)
            strength = _safe_strength(entry.value, base, cfg.sigma_multiplier)
            bucket = per_module.setdefault(
                module,
                {"max_strength": 0.0, "tuples": []},
            )
            bucket["tuples"].append(
                {
                    "module": module,
                    "entity_type": entity_type,
                    "current_rate": entry.value,
                    "baseline_rate": base,
                    "strength": strength,
                }
            )
            if strength > bucket["max_strength"]:
                bucket["max_strength"] = strength

        records: list[SignalRecord] = []
        now = datetime.now(UTC)
        for module, bucket in per_module.items():
            tuples_sorted = sorted(
                bucket["tuples"], key=lambda t: t["strength"], reverse=True
            )
            evidence = {
                "kind_filter": kinds,
                "top_tuples": tuples_sorted[:5],
            }
            strength = bucket["max_strength"]
            grace_metrics.signal_c_strength.set(
                strength, attributes={"ontology_module": module}
            )
            records.append(
                SignalRecord(
                    run_id=run_context.run_id,
                    signal_type="C",
                    ontology_module=module,
                    strength=strength,
                    evidence_snapshot=evidence,
                    detected_at=now,
                )
            )

        return records
