"""extraction_quality_problem detector (D250, D251, D252).

Fires on the conjunction of:
- Signal A strength ≥ 0.5 (extraction confidence regression), AND
- Drop in retrieval-precision proxy ``grace_retrieval_strategy_contributions``
  exceeding ``sigma_multiplier × baseline_std``.

Per D250: ``__global__`` only — ``grace_retrieval_strategy_contributions``
has no ``ontology_module`` label, so per-module attribution would be a
data-source posture violation.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import ClassVar

import structlog

from src.analytics import metrics as grace_metrics
from src.analytics.correlation_engine.base import (
    CorrelationDetector,
    CorrelationRunContext,
    DiagnosticRecord,
    PatternNameLiteral,
    RootCauseModuleLiteral,
)
from src.analytics._prometheus_query_helpers import query_with_coldstart_hint
from src.analytics.correlation_engine.patterns._helpers import (
    fetch_latest_signal_strengths,
)

log = structlog.get_logger(__name__)

_TRIGGER_THRESHOLD = 0.5


class ExtractionQualityProblemDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "extraction_quality_problem"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "extraction"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config

        # Signal A — global aggregate. We average across modules so the
        # detector keeps __global__ semantics (D250).
        a_by_module = fetch_latest_signal_strengths(run_context, "A")
        if not a_by_module:
            grace_metrics.correlation_extraction_quality_problem_strength.set(
                0.0, attributes={"ontology_module": "__global__"}
            )
            return []
        # F-0038/ISS-0027 evidence honesty: keep the argmax entry, not just
        # the value, so the emitted record can name the signal_run the
        # contributing Signal A row came from (window spans multiple runs).
        a_top = max(a_by_module.values(), key=lambda s: s["strength"])
        signal_a_value = a_top["strength"]

        # Retrieval-precision proxy: rolling-rate of contributions over
        # current vs baseline window. A drop indicates retrieval is
        # surfacing fewer high-quality results.
        current_promql = f'sum(rate(grace_retrieval_strategy_contributions_total[{cfg.current_window_days}d]))'
        baseline_promql = f'sum(rate(grace_retrieval_strategy_contributions_total[{cfg.baseline_window_days}d]))'
        current = await run_context.prometheus_reader.query_instant(current_promql)
        baseline = await run_context.prometheus_reader.query_instant(baseline_promql)
        baseline = query_with_coldstart_hint(baseline_promql, baseline)
        baseline_std = await run_context.prometheus_reader.query_instant(
            f'stddev_over_time('
            f'sum(rate(grace_retrieval_strategy_contributions_total[1h]))[{cfg.baseline_window_days}d:1h]'
            f')'
        )

        current_rate = current.entries[0].value if current.entries else 0.0
        baseline_rate = baseline.entries[0].value if baseline.entries else 0.0
        baseline_std_value = (
            baseline_std.entries[0].value if baseline_std.entries else 0.0
        )

        drop = baseline_rate - current_rate
        threshold = cfg.sigma_multiplier * baseline_std_value
        retrieval_dropped = drop >= threshold and threshold > 0.0

        if not retrieval_dropped or signal_a_value < _TRIGGER_THRESHOLD:
            grace_metrics.correlation_extraction_quality_problem_strength.set(
                0.0, attributes={"ontology_module": "__global__"}
            )
            if signal_a_value < _TRIGGER_THRESHOLD or run_context.config.emit_threshold > 0.0:
                return []
            # signal_a_value high but retrieval did not drop — emit gauge,
            # withhold record.
            return []

        retrieval_drop_normalized = (
            min(1.0, drop / baseline_rate) if baseline_rate > 0.0 else 0.0
        )
        strength = max(
            0.0,
            min(1.0, (signal_a_value + retrieval_drop_normalized) / 2.0),
        )

        grace_metrics.correlation_extraction_quality_problem_strength.set(
            strength, attributes={"ontology_module": "__global__"}
        )

        if strength < cfg.emit_threshold:
            return []

        evidence = {
            "signal_a_value": signal_a_value,
            "retrieval_current_rate": current_rate,
            "retrieval_baseline_rate": baseline_rate,
            "retrieval_baseline_std": baseline_std_value,
            "drop": drop,
            "sigma_multiplier": cfg.sigma_multiplier,
        }
        contributing = [
            # F-0038/ISS-0027: record the originating signal_run.
            {
                "signal": "A",
                "strength": signal_a_value,
                "signal_run_id": a_top.get("signal_run_id"),
            },
            {
                "metric": "grace_retrieval_strategy_contributions_total",
                "drop_normalized": retrieval_drop_normalized,
            },
        ]
        summary = (
            f"Extraction Signal A {signal_a_value:.2f} with retrieval "
            f"contributions dropping {drop:.3f}/s (>"
            f"{cfg.sigma_multiplier:.1f}σ baseline)."
        )[:240]

        return [
            DiagnosticRecord(
                run_id=run_context.run_id,
                pattern_name=self.pattern_name,
                ontology_module="__global__",
                suspected_root_cause_module=self.suspected_root_cause_module,
                correlation_strength=strength,
                contributing_signals=contributing,
                evidence_snapshot=evidence,
                human_summary=summary,
                detected_at=datetime.now(UTC),
            )
        ]
