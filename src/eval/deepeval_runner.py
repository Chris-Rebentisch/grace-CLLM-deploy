"""DeepEval regression runner (Chunk 34, D257, D260).

Per ``GoldenCase``:
  1. Build a ``RegenerationQuery`` and call
     ``RegenerationPipeline.regenerate`` (no edits to ``src/regeneration/*``;
     D193 holds — the runner is the consumer side).
  2. Build the ``LLMTestCase.retrieval_context`` from a parallel
     ``RetrievalPipeline.query`` call serialized via ``TemplateSerializer``.
     ``src/retrieval/*`` is read-only here (CF3 holds).
  3. Run five DeepEval metrics against the local Ollama judge.
  4. Compare against per-metric ``warn_floor`` / ``fail_floor`` thresholds.
  5. Emit one ``grace_decompression_faithfulness`` histogram observation
     per faithfulness eval via the shared OTel Meter API instrument
     (``src.analytics.metrics.decompression_faithfulness``). No new
     instrument names — D260 / D173 GOLDEN_NAMES stays at 40.
  6. Per-case timeout (default 60s — see ``EvalConfig.per_case_timeout_seconds``).

The class is intentionally small; tests patch the heavier collaborators
(retrieval pipeline, regeneration pipeline, DeepEval metrics) at the
module boundary.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.analytics import metrics as _metrics
from src.eval.golden_loader import GoldenCase

logger = structlog.get_logger()


_METRIC_NAMES = (
    "contextual_precision",
    "contextual_recall",
    "faithfulness",
    "answer_relevancy",
    "hallucination",
)


@dataclass
class EvalThreshold:
    """Per-metric two-tier threshold (D257)."""

    warn_floor: float
    fail_floor: float
    higher_is_better: bool = True


@dataclass
class EvalConfig:
    """Resolved evaluation suite configuration (D257)."""

    thresholds: dict[str, EvalThreshold]
    judge_provider: str = "ollama"
    judge_model: str = "qwen2.5:7b"
    judge_base_url: str = "http://localhost:11434"
    per_case_timeout_seconds: int = 60
    golden_dataset_dir: str | None = None


@dataclass
class EvalResult:
    """One row in the ``deepeval_results`` table."""

    case_id: str
    query_text: str
    metric_name: str
    metric_score: float
    passed_warn_floor: bool
    passed_fail_floor: bool
    latency_ms: int | None = None


@dataclass
class EvalRun:
    """One row in the ``eval_runs`` table + the per-case results list."""

    id: UUID
    started_at: datetime
    completed_at: datetime | None
    status: str  # running | success | partial_failure | error
    triggered_by: str  # cli | api
    config_hash: str
    golden_dataset_hash: str
    total_cases: int
    passed_warn_floor: int
    passed_fail_floor: int
    results: list[EvalResult] = field(default_factory=list)


def _evaluate_threshold(
    metric_name: str, score: float, threshold: EvalThreshold
) -> tuple[bool, bool]:
    """Return ``(passed_warn_floor, passed_fail_floor)``.

    For metrics where lower is better (hallucination), the floors are
    upper bounds.
    """
    if threshold.higher_is_better:
        return score >= threshold.warn_floor, score >= threshold.fail_floor
    return score <= threshold.warn_floor, score <= threshold.fail_floor


class EvalRunner:
    """Runs the DeepEval suite against a list of ``GoldenCase`` objects.

    Heavier collaborators (retrieval pipeline, regeneration pipeline, the
    DeepEval metrics, the OTel histogram) are accessed via attribute
    references on this class so unit tests can monkeypatch them.
    """

    def __init__(
        self,
        *,
        retrieval_pipeline=None,
        regeneration_pipeline=None,
        serializer=None,
    ) -> None:
        self._retrieval_pipeline = retrieval_pipeline
        self._regeneration_pipeline = regeneration_pipeline
        self._serializer = serializer

    # --- Collaborator accessors (testable) -------------------------------

    def _get_retrieval_pipeline(self):
        if self._retrieval_pipeline is not None:
            return self._retrieval_pipeline
        from src.api.retrieval_routes import _get_pipeline as get_pipeline

        self._retrieval_pipeline = get_pipeline()
        return self._retrieval_pipeline

    def _get_regeneration_pipeline(self):
        if self._regeneration_pipeline is not None:
            return self._regeneration_pipeline
        from src.api.regeneration_routes import _get_pipeline as get_pipeline

        self._regeneration_pipeline = get_pipeline()
        return self._regeneration_pipeline

    def _get_serializer(self):
        if self._serializer is not None:
            return self._serializer
        from src.retrieval.serializer import TemplateSerializer

        self._serializer = TemplateSerializer()
        return self._serializer

    # --- Per-case --------------------------------------------------------

    async def _run_one_case(
        self,
        case: GoldenCase,
        config: EvalConfig,
    ) -> list[EvalResult]:
        """Run all five metrics for a single ``GoldenCase``."""
        from src.regeneration.regeneration_models import RegenerationQuery
        from src.retrieval.retrieval_models import RetrievalQuery

        regen_pipeline = self._get_regeneration_pipeline()
        retrieval_pipeline = self._get_retrieval_pipeline()
        serializer = self._get_serializer()

        regen_query = RegenerationQuery(query_text=case.query_text)
        regen_resp = await regen_pipeline.regenerate(regen_query)

        retrieval_query = RetrievalQuery(query_text=case.query_text, top_k=10)
        retrieval_resp = await retrieval_pipeline.query(retrieval_query)

        results = getattr(retrieval_resp, "results", []) or []
        relationships = getattr(retrieval_resp, "relationships", []) or []
        retrieval_context = serializer.serialize(results, relationships)

        actual_output = getattr(regen_resp, "response_text", "") or ""

        # Build the LLMTestCase. Imported locally so failing DeepEval imports
        # don't poison module load.
        from deepeval.test_case import LLMTestCase

        test_case = LLMTestCase(
            input=case.query_text,
            actual_output=actual_output,
            expected_output=case.expected_output,
            retrieval_context=[retrieval_context] if retrieval_context else [],
            context=[retrieval_context] if retrieval_context else None,
        )

        per_case_results: list[EvalResult] = []

        for metric_name in _METRIC_NAMES:
            t0 = time.perf_counter()
            score = await self._evaluate_metric(metric_name, test_case, config)
            latency_ms = int((time.perf_counter() - t0) * 1000)

            threshold = config.thresholds[metric_name]
            passed_warn, passed_fail = _evaluate_threshold(
                metric_name, score, threshold
            )

            per_case_results.append(
                EvalResult(
                    case_id=case.case_id,
                    query_text=case.query_text,
                    metric_name=metric_name,
                    metric_score=float(score),
                    passed_warn_floor=passed_warn,
                    passed_fail_floor=passed_fail,
                    latency_ms=latency_ms,
                )
            )

            # D260: emit the faithfulness histogram observation in the
            # consumer (here), not in src/regeneration/* (D193 hard-lock).
            if metric_name == "faithfulness":
                try:
                    _metrics.decompression_faithfulness.record(
                        float(score),
                        attributes={
                            "ontology_module": case.ontology_module or "_init",
                            "phase_state": "_init",
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "eval.faithfulness_histogram_record_failed",
                        exc_info=True,
                    )

        return per_case_results

    def _build_judge_model(self, config):
        """Construct the DeepEval judge model from EvalConfig.

        Phase-5 fix: this method previously didn't exist and metrics were
        instantiated without ``model=``, which silently defaulted to
        DeepEval's hard-coded ``GPTModel`` (OpenAI). The
        ``eval_config.yaml judge.{provider,model,base_url}`` settings were
        read into ``EvalConfig`` but never forwarded — see TESTING_LOG.md
        Step 5.3. This method bridges them to the appropriate DeepEval
        wrapper.
        """
        provider = getattr(config, "judge_provider", "ollama")
        model_name = getattr(config, "judge_model", "qwen2.5:7b")
        base_url = getattr(config, "judge_base_url", "")
        if provider == "anthropic":
            import os as _os
            from deepeval.models.llms.anthropic_model import AnthropicModel
            return AnthropicModel(model=model_name, api_key=_os.environ.get("LLM_API_KEY"))
        if provider == "ollama":
            from deepeval.models.llms.ollama_model import OllamaModel
            return OllamaModel(model=model_name, base_url=base_url or "http://localhost:11434")
        if provider == "openai":
            import os as _os
            from deepeval.models.llms.openai_model import GPTModel
            return GPTModel(model=model_name, api_key=_os.environ.get("LLM_API_KEY"))
        raise ValueError(f"unsupported judge provider: {provider!r}")

    async def _evaluate_metric(self, metric_name: str, test_case, config=None) -> float:
        """Evaluate a single DeepEval metric against the configured judge LLM.

        Mocked in unit tests via monkeypatching this method. ``config`` is
        accepted as optional for backward compatibility with tests that call
        this method without it; the production call site always passes it.
        """
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
            HallucinationMetric,
        )

        judge = self._build_judge_model(config) if config is not None else None
        kwargs = {"model": judge} if judge is not None else {}

        if metric_name == "contextual_precision":
            metric = ContextualPrecisionMetric(**kwargs)
        elif metric_name == "contextual_recall":
            metric = ContextualRecallMetric(**kwargs)
        elif metric_name == "faithfulness":
            metric = FaithfulnessMetric(**kwargs)
        elif metric_name == "answer_relevancy":
            metric = AnswerRelevancyMetric(**kwargs)
        elif metric_name == "hallucination":
            metric = HallucinationMetric(**kwargs)
        else:
            raise ValueError(f"Unknown metric: {metric_name!r}")

        # DeepEval metrics expose a synchronous `measure` and an async
        # `a_measure`. Prefer the async path when available.
        a_measure = getattr(metric, "a_measure", None)
        if a_measure is not None:
            await a_measure(test_case)
        else:
            metric.measure(test_case)
        return float(getattr(metric, "score", 0.0))

    # --- Suite -----------------------------------------------------------

    async def run_suite(
        self,
        golden_cases: list[GoldenCase],
        config: EvalConfig,
        *,
        triggered_by: str = "cli",
        config_hash: str = "",
        golden_dataset_hash: str = "",
    ) -> EvalRun:
        """Run the suite over ``golden_cases`` and return an ``EvalRun``."""
        run = EvalRun(
            id=uuid4(),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            status="running",
            triggered_by=triggered_by,
            config_hash=config_hash,
            golden_dataset_hash=golden_dataset_hash,
            total_cases=len(golden_cases),
            passed_warn_floor=0,
            passed_fail_floor=0,
            results=[],
        )

        any_fail = False
        any_error = False

        for case in golden_cases:
            try:
                per_case = await asyncio.wait_for(
                    self._run_one_case(case, config),
                    timeout=config.per_case_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "eval.case.timeout",
                    case_id=case.case_id,
                    timeout=config.per_case_timeout_seconds,
                )
                any_error = True
                continue
            except Exception:  # noqa: BLE001
                logger.exception("eval.case.error", case_id=case.case_id)
                any_error = True
                continue

            run.results.extend(per_case)

        # Aggregate counts at the case level: a case "passes warn-floor" iff
        # every per-metric result for that case passed the warn-floor.
        per_case_metrics: dict[str, list[EvalResult]] = {}
        for r in run.results:
            per_case_metrics.setdefault(r.case_id, []).append(r)
        for case_id, results in per_case_metrics.items():
            if all(r.passed_warn_floor for r in results):
                run.passed_warn_floor += 1
            if all(r.passed_fail_floor for r in results):
                run.passed_fail_floor += 1
            if not all(r.passed_fail_floor for r in results):
                any_fail = True

        run.completed_at = datetime.now(timezone.utc)
        if any_error and not any_fail:
            run.status = "error" if not run.results else "partial_failure"
        elif any_fail:
            run.status = "partial_failure"
        else:
            run.status = "success"

        return run


async def run_suite(
    golden_cases: list[GoldenCase],
    config: EvalConfig,
    *,
    triggered_by: str = "cli",
    config_hash: str = "",
    golden_dataset_hash: str = "",
) -> EvalRun:
    """Module-level convenience wrapper around ``EvalRunner.run_suite``."""
    runner = EvalRunner()
    return await runner.run_suite(
        golden_cases,
        config,
        triggered_by=triggered_by,
        config_hash=config_hash,
        golden_dataset_hash=golden_dataset_hash,
    )
