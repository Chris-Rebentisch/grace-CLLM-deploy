"""ontology_constraint_conflict detector (D535, amends D250).

Per-module conjunction:
- Signal E strength ≥ 0.5 (domain/range violation), AND
- Signal B strength ≥ 0.5 (co-occurrence-without-edge), for the **same**
  module.

Suspected root cause: ``ontology`` (primary). The E+B signature localizes the
fault to the ontology<->extraction boundary but cannot assign blame to one side
from the signals alone, so ``evidence_snapshot.candidate_root_causes`` carries
both ``["ontology", "extraction"]`` and ``boundary_case=True`` (D535 refinement,
surfaced by the A4 Claude-as-correlation-reasoner variance probe — 3/5 independent
reasoners read E+B as ``extraction``).

Capture-the-why (D356) — invariant carve-out:
  Invariant: D250 locked the correlation catalog at FIVE patterns
    ("adding a sixth pattern requires a new D-series amendment",
    ``patterns/__init__.py``).
  Carve-out: this is that sixth pattern.
  Authorization: D535 (amends D250). Surfaced by the Claude-as-LLM A4
    correlation probe — Claude-as-correlation-reasoner correctly
    diagnosed a cross-signal root cause the static 5-pattern library
    missed entirely: domain/range violations (Signal E) co-occurring
    with co-occurrence-without-edge (Signal B) in one module means the
    ontology's relationship domain/range constraints are misaligned with
    what extraction actually observes — extraction either declines the
    edge (B) or writes one that violates the constraint (E). Signal E
    appears in NO other pattern's trigger (only the all-quiet guard of
    ``graph_or_index_problem``), so a high-E module was un-diagnosable.

This is a pure-DB pattern (no Prometheus) — it mirrors
``schema_drift_per_module`` structurally (same-module conjunction of two
signal strengths, strength = mean). It deliberately does NOT collide with
``relationship_gap_propagation`` (which also reads Signal B but fires on
B + a Prometheus zero-results spike, root cause ``extraction``): the two
are complementary root-cause hypotheses over overlapping evidence and key
distinctly on ``pattern_name`` in ``diagnostic_records``.
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


class OntologyConstraintConflictDetector(CorrelationDetector):
    pattern_name: ClassVar[PatternNameLiteral] = "ontology_constraint_conflict"
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral] = "ontology"

    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        cfg = run_context.config
        e_by_module = fetch_latest_signal_strengths(run_context, "E")
        b_by_module = fetch_latest_signal_strengths(run_context, "B")

        modules = set(e_by_module) | set(b_by_module)
        if run_context.target_ontology_modules:
            modules &= set(run_context.target_ontology_modules)

        records: list[DiagnosticRecord] = []
        now = datetime.now(UTC)
        for module in sorted(modules):
            e = e_by_module.get(module, {}).get("strength", 0.0)
            b = b_by_module.get(module, {}).get("strength", 0.0)
            both_high = e >= _TRIGGER_THRESHOLD and b >= _TRIGGER_THRESHOLD
            strength = max(0.0, min(1.0, (e + b) / 2.0)) if both_high else 0.0

            grace_metrics.correlation_ontology_constraint_conflict_strength.set(
                strength, attributes={"ontology_module": module}
            )

            if not both_high or strength < cfg.emit_threshold:
                continue

            # D535 refinement (A4 variance probe): the E+B signature localizes the
            # fault to the ontology<->extraction BOUNDARY but cannot assign blame to
            # one side from the signals alone — wrong ontology domain/range constraints
            # AND buggy extraction produce the identical signature. 3/5 independent
            # Claude reasoners read it as `extraction`; the engine keeps `ontology` as
            # the primary (constraint-inspection entry point + pattern name) but emits
            # both as candidate_root_causes so consumers see the boundary, not a single
            # over-committed verdict. The legend itself flags E as "ontology vs extraction".
            evidence = {
                "ontology_module": module,
                "signal_e_strength": e,
                "signal_b_strength": b,
                "candidate_root_causes": ["ontology", "extraction"],
                "boundary_case": True,
            }
            contributing = [
                # F-0038/ISS-0027 evidence honesty: name the signal_run each
                # contributing signal came from (window spans multiple runs).
                {
                    "signal": "E",
                    "strength": e,
                    "ontology_module": module,
                    "signal_run_id": e_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
                {
                    "signal": "B",
                    "strength": b,
                    "ontology_module": module,
                    "signal_run_id": b_by_module.get(module, {}).get(
                        "signal_run_id"
                    ),
                },
            ]
            summary = (
                f"Ontology<->extraction boundary conflict in '{module}': Signal E "
                f"{e:.2f} (domain/range violation) and Signal B {b:.2f} "
                f"(missing edge) both elevated; root cause is ontology constraints "
                f"OR extraction shaping."
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
