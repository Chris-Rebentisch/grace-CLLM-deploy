"""schema_drift_per_module detector (D250, D252).

Per-module conjunction:
- Signal C strength ≥ 0.5 (type drift), AND
- Signal D strength ≥ 0.5 (deprecation), for the **same** module.

Suspected root cause: ontology.
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
from src.analytics.correlation_engine.patterns._helpers import (
    fetch_latest_signal_strengths,
)

log = structlog.get_logger(__name__)

_TRIGGER_THRESHOLD = 0.5


class SchemaDriftPerModuleDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "schema_drift_per_module"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "ontology"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config
        c_by_module = fetch_latest_signal_strengths(run_context, "C")
        d_by_module = fetch_latest_signal_strengths(run_context, "D")

        modules = set(c_by_module) | set(d_by_module)
        if run_context.target_ontology_modules:
            modules &= set(run_context.target_ontology_modules)

        records: list[DiagnosticRecord] = []
        now = datetime.now(UTC)
        for module in sorted(modules):
            c = c_by_module.get(module, {}).get("strength", 0.0)
            d = d_by_module.get(module, {}).get("strength", 0.0)
            both_high = c >= _TRIGGER_THRESHOLD and d >= _TRIGGER_THRESHOLD
            strength = max(0.0, min(1.0, (c + d) / 2.0)) if both_high else 0.0

            grace_metrics.correlation_schema_drift_per_module_strength.set(
                strength, attributes={"ontology_module": module}
            )

            if not both_high or strength < cfg.emit_threshold:
                continue

            evidence = {
                "ontology_module": module,
                "signal_c_strength": c,
                "signal_d_strength": d,
            }
            contributing = [
                # F-0038/ISS-0027 evidence honesty: name the signal_run each
                # contributing signal came from (window spans multiple runs).
                {
                    "signal": "C",
                    "strength": c,
                    "ontology_module": module,
                    "signal_run_id": c_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
                {
                    "signal": "D",
                    "strength": d,
                    "ontology_module": module,
                    "signal_run_id": d_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
            ]
            summary = (
                f"Schema drift in '{module}': Signal C {c:.2f} and "
                f"Signal D {d:.2f} both elevated."
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
