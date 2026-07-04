"""D298 — Chunk 38 Change_Directives counter registration tests."""

from __future__ import annotations

from src.analytics import metrics


def test_change_directive_created_counter_is_registered():
    metrics.change_directive_created_total.add(
        1, attributes={"tier": "Operational_Adjustment", "outcome": "success"}
    )
    metrics.change_directive_created_total.add(
        1, attributes={"tier": "Strategic_Initiative", "outcome": "error"}
    )


def test_change_directive_transitioned_counter_is_registered():
    metrics.change_directive_transitioned_total.add(
        1, attributes={"from_state": "DRAFT", "to_state": "ACTIVE"}
    )
    metrics.change_directive_transitioned_total.add(
        1, attributes={"from_state": "ACTIVE", "to_state": "REALIZED"}
    )


def test_change_directive_evidence_criterion_compiled_counter_is_registered():
    metrics.change_directive_evidence_criterion_compiled_total.add(
        1,
        attributes={
            "compilation_status": "approved",
            "outcome": "success",
        },
    )
    metrics.change_directive_evidence_criterion_compiled_total.add(
        1,
        attributes={
            "compilation_status": "proposed",
            "outcome": "error",
        },
    )
