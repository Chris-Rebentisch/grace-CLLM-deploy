"""F-38 regression tests: differential CQ gate is the executor default.

The gate mechanism (``run_non_regression_gate(baseline_pass_rate=...)``) landed
in the fix campaign; these tests pin the EXECUTOR wiring — differential by
default, baseline fetched from the active version's latest completed run,
absolute fallback when no baseline exists.
"""

from __future__ import annotations

from src.ontology.cq_test_runner import _gate_decision


def test_gate_decision_differential_non_regression_passes():
    """Honest gap-rich schema (0.55) passes when baseline is 0.567."""
    passed, mode = _gate_decision(
        pass_rate=0.55, threshold=0.90, baseline_pass_rate=0.567, epsilon=0.05
    )
    assert passed is True
    assert mode == "differential"


def test_gate_decision_differential_regression_fails():
    """A proposal that drops the pass rate beyond epsilon is rejected."""
    passed, mode = _gate_decision(
        pass_rate=0.45, threshold=0.90, baseline_pass_rate=0.567, epsilon=0.05
    )
    assert passed is False
    assert mode == "differential"


def test_gate_decision_absolute_without_baseline():
    passed, mode = _gate_decision(
        pass_rate=0.55, threshold=0.90, baseline_pass_rate=None, epsilon=0.05
    )
    assert passed is False
    assert mode == "absolute"


def test_executor_default_config_is_differential():
    """Shipped config declares differential mode; executor default matches."""
    import yaml

    with open("config/change_executor.yaml") as fh:
        cfg = yaml.safe_load(fh)
    assert cfg.get("cq_gate_mode") == "differential"
    assert float(cfg.get("cq_gate_epsilon")) == 0.10  # F2-09 recalibration


def test_executor_source_wires_baseline():
    """apply_proposal fetches get_latest_test_run for the active version and
    threads baseline_pass_rate + epsilon into run_non_regression_gate."""
    import inspect

    from src.ontology import change_executor

    src = inspect.getsource(change_executor)
    assert "get_latest_test_run_in_ancestry" in src  # F2-08 ancestry walk
    assert "baseline_pass_rate=baseline_pass_rate" in src
    assert 'config.get("cq_gate_mode", "differential")' in src
