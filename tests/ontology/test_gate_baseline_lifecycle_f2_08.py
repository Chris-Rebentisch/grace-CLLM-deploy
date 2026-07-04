"""F2-08/F2-09 regression tests: baseline ancestry walk, HITL preservation,
revert exemption, epsilon calibration.

Validation-run evidence: every ratification erased the differential baseline (4
deterministic autonomy bricks incl. the revert safety path), the executor
destroyed the reviewer's `approved` record on gate refusal, and ε=0.05 sat
inside the judge's measured ±2-CQ variance.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.ontology.cq_test_runner import get_latest_test_run_in_ancestry


# ---------------------------------------------------------------------------
# Ancestry walk
# ---------------------------------------------------------------------------


def test_ancestry_walk_finds_parent_baseline():
    """v5 has no runs; its parent v4 does → v4's run is the baseline."""
    v5, v4 = uuid4(), uuid4()
    v4_run = MagicMock(pass_rate=0.233)

    def fake_latest(db, version_id):
        return v4_run if version_id == v4 else None

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (v4,)

    with patch(
        "src.ontology.cq_test_runner.get_latest_test_run",
        side_effect=fake_latest,
    ):
        result = get_latest_test_run_in_ancestry(db, v5)
    assert result is v4_run


def test_ancestry_walk_exhausts_to_none():
    """No runs anywhere in the chain → None (absolute fallback engages)."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (None,)
    with patch(
        "src.ontology.cq_test_runner.get_latest_test_run",
        return_value=None,
    ):
        assert get_latest_test_run_in_ancestry(db, uuid4()) is None


def test_ancestry_walk_bounded():
    """A cyclic/pathological chain terminates at max_depth."""
    v = uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (v,)  # self-loop
    calls = []
    with patch(
        "src.ontology.cq_test_runner.get_latest_test_run",
        side_effect=lambda d, vid: calls.append(vid) or None,
    ):
        assert get_latest_test_run_in_ancestry(db, v, max_depth=5) is None
    assert len(calls) == 5


# ---------------------------------------------------------------------------
# Executor contracts (source-pinned, matching the F-38 test style)
# ---------------------------------------------------------------------------


def _executor_src() -> str:
    from src.ontology import change_executor

    return inspect.getsource(change_executor)


def test_executor_uses_ancestry_baseline():
    src = _executor_src()
    assert "get_latest_test_run_in_ancestry" in src


def test_gate_refusal_preserves_approved_status():
    """Gate refusal must keep the HITL decision, never flip to REJECTED."""
    src = _executor_src()
    assert "cq_gate_refusal" in src
    # The refusal block sets APPROVED, and the old rejection_reason flip is gone.
    assert '"rejection_reason": "CQ non-regression gate failed"' not in src


def test_revert_proposals_skip_the_gate():
    src = _executor_src()
    assert 'reviewer", None) == "system:revert"' in src
    assert "cq_gate_skipped_for_revert" in src


def test_epsilon_default_above_judge_noise():
    """F2-09: ε default must exceed the measured ±2-CQ noise band (>=0.10)."""
    import yaml

    with open("config/change_executor.yaml") as fh:
        cfg = yaml.safe_load(fh)
    assert float(cfg.get("cq_gate_epsilon")) >= 0.10
    assert 'config.get("cq_gate_epsilon", 0.10)' in _executor_src()
