"""relationship_gap_propagation detector (D250, D252).

Hybrid module + global conjunction:
- Per-module Signal B strength ≥ 0.5
  (co-occurrence-without-edge), AND
- Global ``grace_retrieval_zero_results`` rate increase exceeding
  ``sigma_multiplier × baseline_std``.

Suspected root cause: ``extraction``.
"""

from __future__ import annotations

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


class RelationshipGapPropagationDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "relationship_gap_propagation"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "extraction"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config

        b_by_module = fetch_latest_signal_strengths(run_context, "B")
        if not b_by_module:
            return []

        # Global zero-results rate.
        current_promql = (
            f'sum(rate(grace_retrieval_zero_results_total[{cfg.current_window_days}d]))'
        )
        baseline_promql = (
            f'sum(rate(grace_retrieval_zero_results_total[{cfg.baseline_window_days}d]))'
        )
        baseline_std_promql = (
            f'stddev_over_time(sum(rate(grace_retrieval_zero_results_total[1h]))'
            f'[{cfg.baseline_window_days}d:1h])'
        )
        current = await run_context.prometheus_reader.query_instant(current_promql)
        baseline = await run_context.prometheus_reader.query_instant(baseline_promql)
        baseline = query_with_coldstart_hint(baseline_promql, baseline)
        baseline_std = await run_context.prometheus_reader.query_instant(
            baseline_std_promql
        )

        current_rate = current.entries[0].value if current.entries else 0.0
        baseline_rate = baseline.entries[0].value if baseline.entries else 0.0
        baseline_std_value = (
            baseline_std.entries[0].value if baseline_std.entries else 0.0
        )
        increase = current_rate - baseline_rate
        threshold = cfg.sigma_multiplier * baseline_std_value
        zero_results_spike = increase > threshold and threshold > 0.0

        modules = set(b_by_module)
        if run_context.target_ontology_modules:
            modules &= set(run_context.target_ontology_modules)

        records: list[DiagnosticRecord] = []
        now = datetime.now(UTC)

        for module in sorted(modules):
            b = b_by_module.get(module, {}).get("strength", 0.0)
            if b < _TRIGGER_THRESHOLD or not zero_results_spike:
                grace_metrics.correlation_relationship_gap_propagation_strength.set(
                    0.0, attributes={"ontology_module": module}
                )
                continue

            zero_results_normalized = (
                min(1.0, increase / baseline_rate)
                if baseline_rate > 0.0
                else 1.0
            )
            strength = max(0.0, min(1.0, (b + zero_results_normalized) / 2.0))

            grace_metrics.correlation_relationship_gap_propagation_strength.set(
                strength, attributes={"ontology_module": module}
            )
            if strength < cfg.emit_threshold:
                continue

            evidence = {
                "ontology_module": module,
                "signal_b_strength": b,
                "zero_results_current_rate": current_rate,
                "zero_results_baseline_rate": baseline_rate,
                "zero_results_baseline_std": baseline_std_value,
                "increase": increase,
                "sigma_multiplier": cfg.sigma_multiplier,
            }
            contributing = [
                # F-0038/ISS-0027 evidence honesty: name the signal_run the
                # contributing signal came from (window spans multiple runs).
                {
                    "signal": "B",
                    "strength": b,
                    "ontology_module": module,
                    "signal_run_id": b_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
                {
                    "metric": "grace_retrieval_zero_results_total",
                    "increase": increase,
                },
            ]
            summary = (
                f"Relationship gaps in '{module}': Signal B {b:.2f} while "
                f"global zero-results rate up {increase:.3f}/s "
                f"(>{cfg.sigma_multiplier:.1f}σ)."
            )[:240]
            records.append(
                DiagnosticRecord(
                    run_id=run_context.run_id,
                    pattern_name=self.pattern_name,
                    ontology_module=module,
                    suspected_root_cause_module=self.suspected_root_cause_module,
                    correlation_strength=strength,
                    contributing_signals=contributing,
                    evidence_snapshot=evidence,
                    human_summary=summary,
                    detected_at=now,
                )
            )

        return records
