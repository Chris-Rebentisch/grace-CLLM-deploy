"""cq_regression_pre_extraction detector (D250, D251, D252).

Per-module conjunction:
- Signal F strength ≥ 0.5 (CQ regression / discovery gaps), AND
- Extraction throughput remains stable — Mann-Kendall trend is **not**
  ``decreasing`` over the baseline window.

Suspected root cause: ``discovery``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pymannkendall as mk
import structlog

from src.analytics import metrics as grace_metrics
from src.analytics.correlation_engine.base import (
    CorrelationDetector,
    CorrelationRunContext,
    DiagnosticRecord,
    PatternNameLiteral,
    RootCauseModuleLiteral,
)
from src.analytics.correlation_engine.patterns._helpers import (
    fetch_latest_signal_strengths,
)

log = structlog.get_logger(__name__)

_TRIGGER_THRESHOLD = 0.5


def _clean_series(values: list[float]) -> list[float]:
    """Replace NaN with 0 (R9)."""
    return [0.0 if v is None or math.isnan(v) else float(v) for v in values]


class CQRegressionPreExtractionDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "cq_regression_pre_extraction"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "discovery"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config
        f_by_module = fetch_latest_signal_strengths(run_context, "F")
        if not f_by_module:
            return []

        modules = set(f_by_module)
        if run_context.target_ontology_modules:
            modules &= set(run_context.target_ontology_modules)

        records: list[DiagnosticRecord] = []
        now = datetime.now(UTC)
        for module in sorted(modules):
            f = f_by_module.get(module, {}).get("strength", 0.0)

            if f < _TRIGGER_THRESHOLD:
                grace_metrics.correlation_cq_regression_pre_extraction_strength.set(
                    0.0, attributes={"ontology_module": module}
                )
                continue

            # Mann-Kendall on extraction throughput rate (histogram _count
            # sub-series). One point per day across the baseline window.
            end = now
            start = end - timedelta(days=cfg.baseline_window_days)
            promql = (
                f'sum(rate(grace_extraction_triple_confidence_count'
                f'{{ontology_module="{module}"}}[1d]))'
            )
            matrix = await run_context.prometheus_reader.query_range(
                promql, start=start, end=end, step="1d"
            )
            series: list[float] = []
            if matrix.entries:
                series = _clean_series([v for _, v in matrix.entries[0].values])

            trend: str
            mk_p_value: float | None = None
            mk_skipped = False
            if len(series) >= cfg.mann_kendall_min_points:
                result = mk.original_test(series, alpha=cfg.mann_kendall_alpha)
                trend = result.trend
                mk_p_value = float(result.p) if result.p is not None else None
            else:
                # Not enough samples — assume stable so we don't suppress
                # the pattern (R5).
                trend = "no trend"
                mk_skipped = True

            throughput_stable = trend != "decreasing"
            if not throughput_stable:
                grace_metrics.correlation_cq_regression_pre_extraction_strength.set(
                    0.0, attributes={"ontology_module": module}
                )
                continue

            strength = max(0.0, min(1.0, f))

            grace_metrics.correlation_cq_regression_pre_extraction_strength.set(
                strength, attributes={"ontology_module": module}
            )

            if strength < cfg.emit_threshold:
                continue

            evidence = {
                "ontology_module": module,
                "signal_f_strength": f,
                "throughput_trend": trend,
                "mk_p_value": mk_p_value,
                "mk_alpha": cfg.mann_kendall_alpha,
                "mk_skipped_insufficient_samples": mk_skipped,
                "samples": len(series),
            }
            contributing = [
                # F-0038/ISS-0027 evidence honesty: name the signal_run the
                # contributing signal came from (window spans multiple runs).
                {
                    "signal": "F",
                    "strength": f,
                    "ontology_module": module,
                    "signal_run_id": f_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
                {
                    "metric": "grace_extraction_triple_confidence_count",
                    "trend": trend,
                },
            ]
            summary = (
                f"CQ regression in '{module}': Signal F {f:.2f} while "
                f"extraction throughput trend = {trend}."
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
