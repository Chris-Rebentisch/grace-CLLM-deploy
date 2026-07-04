"""Tests for RegenerationPipeline (§8 of chunk-23-spec.md)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    RegenerationQuery,
    RegenOverrides,
)
from src.regeneration.regeneration_pipeline import (
    AssembleStageError,
    RegenerationPipeline,
    RetrievalStageError,
    SynthesizeStageError,
)
from src.retrieval.retrieval_models import (
    RankedResult,
    RetrievalResponse,
)
from src.shared.llm_provider import LLMResponse


def _retrieval_response(
    results: list[RankedResult] | None = None,
    context: str = "some context",
) -> RetrievalResponse:
    return RetrievalResponse(
        query="q",
        results=results or [],
        serialized_context=context,
        serialization_format="template",
        total_candidates=len(results or []),
        strategy_contributions={"graph": 1},
        latency_ms={"retrieval_total": 100.0},
        retrieval_mode="single_round",
    )


def _ranked(name: str, grace_id: str, rerank: float) -> RankedResult:
    return RankedResult(
        grace_id=grace_id,
        entity_type="Company",
        name=name,
        rerank_score=rerank,
        rrf_score=0.0,
        contributing_strategies=["semantic"],
    )


def _mock_retrieval_pipeline(
    response: RetrievalResponse | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    pipeline = MagicMock()
    if side_effect is not None:
        pipeline.query = AsyncMock(side_effect=side_effect)
    else:
        pipeline.query = AsyncMock(return_value=response)
    return pipeline


def _build_pipeline(
    settings: RegenSettings,
    retrieval_mock: MagicMock,
) -> RegenerationPipeline:
    return RegenerationPipeline(
        retrieval_pipeline=retrieval_mock, settings=settings
    )


@pytest.mark.asyncio
async def test_happy_path_returns_valid_response() -> None:
    settings = RegenSettings()
    retr_response = _retrieval_response(
        results=[_ranked("Apple", "g1", 0.9)],
        context="Apple is a company.",
    )
    retr_mock = _mock_retrieval_pipeline(response=retr_response)
    provider = AsyncMock()
    provider.generate = AsyncMock(
        return_value=LLMResponse(
            text="Apple is mentioned here.",
            model="qwen2.5:7b",
            provider="ollama",
            input_tokens=100,
            output_tokens=20,
        )
    )
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        pipe = _build_pipeline(settings, retr_mock)
        resp = await pipe.regenerate(
            RegenerationQuery(query_text="What is Apple?")
        )

    assert resp.response_text == "Apple is mentioned here."
    assert resp.phase_state == "none"
    assert resp.model == "qwen2.5:7b"
    assert resp.provider == "ollama"
    assert "retrieve" in resp.latency_ms
    assert "assemble" in resp.latency_ms
    assert "synthesize" in resp.latency_ms
    assert "span_detect" in resp.latency_ms
    assert "total" in resp.latency_ms
    assert resp.response_metadata.phase_style_applied == (
        settings.phase_style_none
    )
    assert resp.claim_spans  # at least one span detected
    assert resp.token_usage["input_tokens"] == 100
    assert resp.token_usage["output_tokens"] == 20


@pytest.mark.asyncio
async def test_retrieval_failure_raises_retrieval_stage_error() -> None:
    settings = RegenSettings()
    retr_mock = _mock_retrieval_pipeline(
        side_effect=ConnectionError("arcade down")
    )
    pipe = _build_pipeline(settings, retr_mock)
    with pytest.raises(RetrievalStageError) as exc_info:
        await pipe.regenerate(RegenerationQuery(query_text="q"))
    assert "retrieve" in exc_info.value.stage_latencies
    assert exc_info.value.stage_latencies["retrieve"] >= 0.0
    assert exc_info.value.stage == "retrieve"


@pytest.mark.asyncio
async def test_assemble_failure_includes_retrieve_timing() -> None:
    settings = RegenSettings(total_input_budget_tokens=5)
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(context="ctx")
    )
    pipe = _build_pipeline(settings, retr_mock)
    # Query text is long enough that system+query alone exceed the 5-token
    # budget, forcing PromptAssemblyError → AssembleStageError subclass.
    with pytest.raises(AssembleStageError) as exc_info:
        await pipe.regenerate(
            RegenerationQuery(query_text="x" * 2000)
        )
    assert "retrieve" in exc_info.value.stage_latencies


@pytest.mark.asyncio
async def test_synthesize_failure_includes_prior_timings() -> None:
    settings = RegenSettings()
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(context="ctx")
    )
    provider = AsyncMock()
    provider.generate = AsyncMock(side_effect=RuntimeError("ollama down"))
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        pipe = _build_pipeline(settings, retr_mock)
        with pytest.raises(SynthesizeStageError) as exc_info:
            await pipe.regenerate(RegenerationQuery(query_text="q"))
    assert "retrieve" in exc_info.value.stage_latencies
    assert "assemble" in exc_info.value.stage_latencies


@pytest.mark.asyncio
async def test_span_detect_failure_degrades_to_note_not_error() -> None:
    settings = RegenSettings()
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(
            results=[_ranked("Apple", "g1", 0.9)], context="ctx"
        )
    )
    provider = AsyncMock()
    provider.generate = AsyncMock(
        return_value=LLMResponse(text="Apple.", model="m", provider="ollama")
    )
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        pipe = _build_pipeline(settings, retr_mock)
        # Force span detector to raise.
        pipe.span_detector.detect = MagicMock(
            side_effect=ValueError("boom")
        )
        resp = await pipe.regenerate(RegenerationQuery(query_text="q"))
    assert resp.claim_spans == []
    assert resp.response_metadata.span_detection_note == (
        "span_detection_degraded"
    )


@pytest.mark.asyncio
async def test_contributing_grace_ids_deduplicated_across_spans() -> None:
    settings = RegenSettings()
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(
            results=[
                _ranked("Apple", "g1", 0.9),
                _ranked("Microsoft", "g2", 0.6),
            ],
            context="ctx",
        )
    )
    provider = AsyncMock()
    # Response text mentions Apple twice and Microsoft once.
    provider.generate = AsyncMock(
        return_value=LLMResponse(
            text="Apple is mentioned. Microsoft is also here. Apple again.",
            model="m",
            provider="ollama",
        )
    )
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        pipe = _build_pipeline(settings, retr_mock)
        resp = await pipe.regenerate(RegenerationQuery(query_text="q"))
    # g1 referenced twice but only once in contributing_grace_ids
    assert sorted(resp.contributing_grace_ids) == ["g1", "g2"]


@pytest.mark.asyncio
async def test_model_override_applied_false_in_v1() -> None:
    settings = RegenSettings()
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(context="ctx")
    )
    provider = AsyncMock()
    provider.generate = AsyncMock(
        return_value=LLMResponse(text="x", model="m", provider="ollama")
    )
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        pipe = _build_pipeline(settings, retr_mock)
        resp = await pipe.regenerate(
            RegenerationQuery(
                query_text="q",
                overrides=RegenOverrides(regeneration_model="llama3:8b"),
            )
        )
    assert resp.response_metadata.model_override_applied is False


@pytest.mark.asyncio
async def test_debug_log_prompts_false_hides_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # structlog routes to stdlib logger by default; just verify the full
    # prompt does NOT appear in any captured log record message.
    settings = RegenSettings(debug_log_prompts=False)
    retr_mock = _mock_retrieval_pipeline(
        response=_retrieval_response(
            context="SECRET_CONTEXT_TOKEN_xyz"
        )
    )
    provider = AsyncMock()
    provider.generate = AsyncMock(
        return_value=LLMResponse(text="out", model="m", provider="ollama")
    )
    with caplog.at_level(logging.DEBUG):
        with patch(
            "src.regeneration.response_synthesizer.get_provider",
            return_value=provider,
        ):
            pipe = _build_pipeline(settings, retr_mock)
            await pipe.regenerate(RegenerationQuery(query_text="q"))
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_CONTEXT_TOKEN_xyz" not in combined
