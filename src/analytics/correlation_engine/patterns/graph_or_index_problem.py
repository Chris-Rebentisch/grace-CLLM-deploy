"""graph_or_index_problem detector (D250, D251, D252).

Fires when retrieval p95 latency exceeds baseline + sigma×std AND **all
six** signal strengths (A–F) are below 0.3 — the smoking-gun signature
of a graph or index issue (latency spike that is not explained by any
upstream extraction/ontology/CQ signal).

Per D250: ``__global__`` only — latency aggregates are not
``ontology_module``-labeled.
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

_SIGNALS_LOW_THRESHOLD = 0.3


class GraphOrIndexProblemDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "graph_or_index_problem"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "graph"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config

        # Latency p95 — current vs baseline mean+std.
        current_promql = (
            'histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket'
            f'{{http_route=~"/api/retrieval/.*"}}[{cfg.current_window_days}d])) by (le))'
        )
        baseline_promql = (
            'avg_over_time(histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket'
            f'{{http_route=~"/api/retrieval/.*"}}[1h])) by (le))[{cfg.baseline_window_days}d:1h])'
        )
        baseline_std_promql = (
            'stddev_over_time(histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket'
            f'{{http_route=~"/api/retrieval/.*"}}[1h])) by (le))[{cfg.baseline_window_days}d:1h])'
        )

        current = await run_context.prometheus_reader.query_instant(current_promql)
        baseline = await run_context.prometheus_reader.query_instant(baseline_promql)
        baseline = query_with_coldstart_hint(baseline_promql, baseline)
        baseline_std = await run_context.prometheus_reader.query_instant(
            baseline_std_promql
        )

        current_p95 = current.entries[0].value if current.entries else 0.0
        baseline_p95 = baseline.entries[0].value if baseline.entries else 0.0
        baseline_std_value = (
            baseline_std.entries[0].value if baseline_std.entries else 0.0
        )
        threshold = baseline_p95 + cfg.sigma_multiplier * baseline_std_value

        latency_spike = current_p95 > threshold and threshold > 0.0

        # All six signals — aggregate to global max per signal type.
        # F-0038/ISS-0027 evidence honesty: also keep the signal_run_id of the
        # max-strength row per signal (window spans multiple runs).
        signal_maxes: dict[str, float] = {}
        signal_run_ids: dict[str, str | None] = {}
        for sig in ("A", "B", "C", "D", "E", "F"):
            by_module = fetch_latest_signal_strengths(run_context, sig)
            if by_module:
                top = max(by_module.values(), key=lambda s: s["strength"])
                signal_maxes[sig] = top["strength"]
                signal_run_ids[sig] = top.get("signal_run_id")
            else:
                signal_maxes[sig] = 0.0
                signal_run_ids[sig] = None
        max_signal = max(signal_maxes.values()) if signal_maxes else 0.0
        signals_low = max_signal < _SIGNALS_LOW_THRESHOLD

        if not (latency_spike and signals_low):
            grace_metrics.correlation_graph_or_index_problem_strength.set(
                0.0, attributes={"ontology_module": "__global__"}
            )
            return []

        latency_normalized = (
            min(1.0, (current_p95 - baseline_p95) / max(baseline_p95, 1e-9))
            if baseline_p95 > 0.0
            else 1.0
        )
        signal_complement = 1.0 - max_signal  # higher when signals are quieter
        strength = max(0.0, min(1.0, (latency_normalized + signal_complement) / 2.0))

        grace_metrics.correlation_graph_or_index_problem_strength.set(
            strength, attributes={"ontology_module": "__global__"}
        )

        if strength < cfg.emit_threshold:
            return []

        evidence = {
            "current_p95_seconds": current_p95,
            "baseline_p95_seconds": baseline_p95,
            "baseline_std_seconds": baseline_std_value,
            "sigma_multiplier": cfg.sigma_multiplier,
            "signal_maxes": signal_maxes,
        }
        contributing = [
            {
                "metric": "http_server_request_duration_seconds",
                "p95_current": current_p95,
                "p95_baseline": baseline_p95,
            },
            *[
                # F-0038/ISS-0027: record the originating signal_run.
                {"signal": k, "strength": v, "signal_run_id": signal_run_ids.get(k)}
                for k, v in signal_maxes.items()
            ],
        ]
        summary = (
            f"Retrieval p95 {current_p95:.3f}s vs baseline {baseline_p95:.3f}s "
            f"(>{cfg.sigma_multiplier:.1f}σ) and all signals quiet "
            f"(max={max_signal:.2f})."
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
