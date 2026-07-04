"""DeepEval runner tests (Chunk 34, D257, D260).

All heavy collaborators (DeepEval metrics, retrieval pipeline, regeneration
pipeline) are mocked. The runner's responsibilities under test:

  1. End-to-end mocked run produces one EvalResult per (case, metric).
  2. Warn-floor and fail-floor flags computed correctly per metric.
  3. Faithfulness histogram observation captured.
  4. Per-case timeout marks the run as partial_failure / error.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.eval.deepeval_runner import (
    EvalConfig,
    EvalRunner,
    EvalThreshold,
    _METRIC_NAMES,
)
from src.eval.golden_loader import GoldenCase


def _config(threshold_map: dict[str, EvalThreshold] | None = None) -> EvalConfig:
    if threshold_map is None:
        threshold_map = {
            name: EvalThreshold(
                warn_floor=0.80, fail_floor=0.70,
                higher_is_better=(name != "hallucination"),
            )
            for name in _METRIC_NAMES
        }
        # Hallucination is "lower is better".
        threshold_map["hallucination"] = EvalThreshold(
            warn_floor=0.15, fail_floor=0.25, higher_is_better=False
        )
    return EvalConfig(
        thresholds=threshold_map,
        per_case_timeout_seconds=2,
    )


def _case(case_id: str = "c1") -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        query_text=f"query for {case_id}",
        expected_output="expected",
        expected_retrieval_path="graph",
        query_complexity="simple",
        ontology_module="corporate_structure",
        source_documents=[],
        notes=None,
    )


def _mock_runner(*, score_map: dict[str, float] | None = None) -> EvalRunner:
    if score_map is None:
        score_map = {name: 0.95 for name in _METRIC_NAMES}
        # Hallucination is "lower is better"; default to a passing low value.
        score_map["hallucination"] = 0.05

    retrieval_pipeline = MagicMock()
    retrieval_pipeline.query = AsyncMock(
        return_value=MagicMock(results=[], relationships=[])
    )

    regen_pipeline = MagicMock()
    regen_pipeline.regenerate = AsyncMock(
        return_value=MagicMock(response_text="actual answer")
    )

    serializer = MagicMock()
    serializer.serialize = MagicMock(return_value="ENTITY: foo")

    runner = EvalRunner(
        retrieval_pipeline=retrieval_pipeline,
        regeneration_pipeline=regen_pipeline,
        serializer=serializer,
    )

    async def _fake_evaluate_metric(metric_name, test_case, config):
        return float(score_map[metric_name])

    runner._evaluate_metric = _fake_evaluate_metric  # type: ignore[assignment]
    return runner


@pytest.mark.asyncio
async def test_end_to_end_mock_run_yields_one_result_per_metric():
    runner = _mock_runner()
    config = _config()
    cases = [_case("c1"), _case("c2")]

    run = await runner.run_suite(cases, config, triggered_by="cli")

    assert run.total_cases == 2
    assert len(run.results) == 2 * len(_METRIC_NAMES)
    metric_set = {r.metric_name for r in run.results}
    assert metric_set == set(_METRIC_NAMES)
    assert run.status == "success"
    assert run.passed_warn_floor == 2
    assert run.passed_fail_floor == 2


@pytest.mark.asyncio
async def test_warn_and_fail_floor_flags():
    """A faithfulness score between fail-floor and warn-floor flips warn flag.

    Score 0.78, warn_floor 0.85, fail_floor 0.75:
      passed_warn_floor=False, passed_fail_floor=True.
    """
    score_map = {name: 0.95 for name in _METRIC_NAMES}
    score_map["faithfulness"] = 0.78
    score_map["hallucination"] = 0.05
    runner = _mock_runner(score_map=score_map)
    config = _config(
        {
            **{
                n: EvalThreshold(warn_floor=0.80, fail_floor=0.70)
                for n in _METRIC_NAMES
            },
            "faithfulness": EvalThreshold(warn_floor=0.85, fail_floor=0.75),
            "hallucination": EvalThreshold(
                warn_floor=0.15, fail_floor=0.25, higher_is_better=False
            ),
        }
    )

    run = await runner.run_suite([_case("c1")], config)
    faithfulness_row = [r for r in run.results if r.metric_name == "faithfulness"][0]
    assert faithfulness_row.metric_score == pytest.approx(0.78)
    assert faithfulness_row.passed_warn_floor is False
    assert faithfulness_row.passed_fail_floor is True


@pytest.mark.asyncio
async def test_faithfulness_histogram_observation_captured():
    """When a faithfulness score is recorded, ``decompression_faithfulness.record``
    is invoked exactly once per case.
    """
    runner = _mock_runner()
    config = _config()

    with patch(
        "src.eval.deepeval_runner._metrics.decompression_faithfulness"
    ) as mock_hist:
        await runner.run_suite([_case("c1"), _case("c2")], config)

    # Two cases × one faithfulness metric = 2 record calls.
    assert mock_hist.record.call_count == 2
    # Inspect the second call: the attribute dict must include
    # ontology_module and phase_state (required by the D151 _init filter).
    args, kwargs = mock_hist.record.call_args_list[0]
    attrs = kwargs.get("attributes", args[1] if len(args) > 1 else {})
    assert "ontology_module" in attrs
    assert "phase_state" in attrs


@pytest.mark.asyncio
async def test_per_case_timeout_marks_run_partial_failure():
    """When a case's evaluation exceeds ``per_case_timeout_seconds``, the
    run finishes with ``error``/``partial_failure`` and no result rows
    for that case."""
    retrieval_pipeline = MagicMock()
    retrieval_pipeline.query = AsyncMock(
        return_value=MagicMock(results=[], relationships=[])
    )
    regen_pipeline = MagicMock()
    regen_pipeline.regenerate = AsyncMock(
        side_effect=lambda *args, **kwargs: asyncio.sleep(5)
    )
    serializer = MagicMock()
    serializer.serialize = MagicMock(return_value="")

    runner = EvalRunner(
        retrieval_pipeline=retrieval_pipeline,
        regeneration_pipeline=regen_pipeline,
        serializer=serializer,
    )

    config = _config()
    config.per_case_timeout_seconds = 0  # force timeout

    run = await runner.run_suite([_case("c1")], config)
    assert run.results == []
    assert run.status in {"error", "partial_failure"}
