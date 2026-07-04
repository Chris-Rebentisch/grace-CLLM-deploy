"""F-38 regression: the CQ non-regression gate must support DIFFERENTIAL mode.

An honest, gap-rich schema scores ~0.567 even with a good judge (the deliberate
golden gaps are real absences), so under an absolute 0.90 gate no proposal can
ever pass — the whole Signal→Proposal→Execute loop was dead. Differential mode
passes a proposal when it does not regress answerability beyond epsilon
(proposed >= baseline - epsilon). Absolute mode is retained when no baseline.
"""

from __future__ import annotations

from src.ontology.cq_test_runner import _gate_decision, verbalize_schema


def test_verbalized_schema_states_edge_temporal_properties():
    """F-38 half 2: the judge must know edges carry system valid_from/valid_to
    (else temporal 'since when' CQs falsely FAIL)."""
    out = verbalize_schema(
        {
            "entity_types": {"Lease": {"description": "d"}},
            "relationships": {"leases": {"source_type": "P", "target_type": "U"}},
        }
    )
    assert "valid_from" in out and "valid_to" in out
    # No relationships → no temporal note.
    assert "valid_from" not in verbalize_schema({"entity_types": {"X": {}}})


def test_absolute_mode_unchanged_when_no_baseline():
    passed, mode = _gate_decision(0.567, threshold=0.90, baseline_pass_rate=None, epsilon=0.05)
    assert mode == "absolute"
    assert passed is False  # 0.567 < 0.90 (the broken-by-design case)

    passed, mode = _gate_decision(0.95, threshold=0.90, baseline_pass_rate=None, epsilon=0.05)
    assert passed is True and mode == "absolute"


def test_differential_passes_non_regressing_proposal():
    # Proposed matches the honest baseline → passes despite being < 0.90.
    passed, mode = _gate_decision(0.567, threshold=0.90, baseline_pass_rate=0.567, epsilon=0.05)
    assert mode == "differential"
    assert passed is True


def test_differential_tolerates_small_dip_within_epsilon():
    passed, _ = _gate_decision(0.55, threshold=0.90, baseline_pass_rate=0.58, epsilon=0.05)
    assert passed is True  # 0.55 >= 0.58 - 0.05


def test_differential_rejects_real_regression():
    passed, _ = _gate_decision(0.40, threshold=0.90, baseline_pass_rate=0.58, epsilon=0.05)
    assert passed is False  # 0.40 < 0.58 - 0.05
