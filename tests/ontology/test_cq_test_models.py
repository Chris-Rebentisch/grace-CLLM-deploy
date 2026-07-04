"""Unit tests for CQ Test Runner Pydantic models and enums."""

from uuid import uuid4

from src.ontology.cq_test_models import (
    CQGapSeverity,
    CQGapType,
    CQTestGateResult,
    CQTestResult,
    CQTestResultEntry,
    CQTestRun,
    CQTestRunStatus,
)


def test_cq_test_result_entry_all_result_types():
    """CQTestResultEntry accepts all result types."""
    for result in CQTestResult:
        entry = CQTestResultEntry(
            cq_id="cq_001",
            cq_text="What is X?",
            result=result,
        )
        assert entry.result == result


def test_cq_test_result_entry_gap_fields_accept_none():
    """CQTestResultEntry gap_type and gap_severity accept None for passing CQs."""
    entry = CQTestResultEntry(
        cq_id="cq_001",
        cq_text="What is X?",
        result=CQTestResult.PASS,
        confidence=0.95,
        traced_path="Company -> (owns) -> Subsidiary",
    )
    assert entry.gap_type is None
    assert entry.gap_severity is None
    assert entry.traced_path is not None


def test_cq_test_run_default_status():
    """CQTestRun default status is RUNNING."""
    run = CQTestRun(schema_version_id=uuid4())
    assert run.status == CQTestRunStatus.RUNNING
    assert run.total_cqs == 0
    assert run.pass_rate == 0.0


def test_cq_test_run_pass_rate():
    """CQTestRun pass_rate field stores correctly."""
    run = CQTestRun(
        schema_version_id=uuid4(),
        total_cqs=10,
        passing=8,
        failing=1,
        out_of_scope=1,
        pass_rate=8 / 9,  # 8 / (10 - 1 out_of_scope)
    )
    assert run.pass_rate > 0.88
    assert run.pass_rate < 0.90


def test_cq_test_gate_result():
    """CQTestGateResult gate_passed logic."""
    gate_pass = CQTestGateResult(
        gate_passed=True,
        pass_rate=0.95,
        threshold=0.90,
        total_cqs=20,
        passing=19,
        failing=1,
        test_run_id=uuid4(),
    )
    assert gate_pass.gate_passed is True

    gate_fail = CQTestGateResult(
        gate_passed=False,
        pass_rate=0.80,
        threshold=0.90,
        total_cqs=20,
        passing=16,
        failing=4,
        test_run_id=uuid4(),
    )
    assert gate_fail.gate_passed is False
