"""Tests for ResponseSynthesizer (§6 of chunk-23-spec.md)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    AssembledPrompt,
    RegenOverrides,
)
from src.regeneration.response_synthesizer import ResponseSynthesizer
from src.shared.llm_provider import LLMResponse


def _assembled() -> AssembledPrompt:
    return AssembledPrompt(
        system_prompt="SYS",
        context="CTX",
        user_query="Q?",
        system_token_estimate=1,
        context_token_estimate=1,
        query_token_estimate=1,
        total_token_estimate=3,
        phase_style_applied="d",
    )


def _mock_provider_returning(resp: LLMResponse) -> AsyncMock:
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value=resp)
    return provider


@pytest.mark.asyncio
async def test_synthesize_calls_provider_with_json_mode_false() -> None:
    resp = LLMResponse(text="hello", model="m", provider="ollama")
    provider = _mock_provider_returning(resp)
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        syn = ResponseSynthesizer(RegenSettings())
        await syn.synthesize(_assembled())
    provider.generate.assert_awaited_once()
    kwargs = provider.generate.await_args.kwargs
    assert kwargs["json_mode"] is False


@pytest.mark.asyncio
async def test_overrides_temperature_wins() -> None:
    resp = LLMResponse(text="x", model="m", provider="ollama")
    provider = _mock_provider_returning(resp)
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        syn = ResponseSynthesizer(RegenSettings(regeneration_temperature=0.3))
        await syn.synthesize(
            _assembled(), RegenOverrides(temperature=0.9)
        )
    kwargs = provider.generate.await_args.kwargs
    assert kwargs["temperature"] == 0.9


@pytest.mark.asyncio
async def test_overrides_response_max_tokens_wins() -> None:
    resp = LLMResponse(text="x", model="m", provider="ollama")
    provider = _mock_provider_returning(resp)
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        syn = ResponseSynthesizer(RegenSettings(response_budget_tokens=100))
        await syn.synthesize(
            _assembled(), RegenOverrides(response_max_tokens=555)
        )
    kwargs = provider.generate.await_args.kwargs
    assert kwargs["max_tokens"] == 555


@pytest.mark.asyncio
async def test_provider_exceptions_propagate() -> None:
    provider = AsyncMock()
    provider.generate = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        syn = ResponseSynthesizer(RegenSettings())
        with pytest.raises(RuntimeError, match="boom"):
            await syn.synthesize(_assembled())


@pytest.mark.asyncio
async def test_returned_llm_response_passed_through_unmodified() -> None:
    resp = LLMResponse(
        text="the-text",
        model="qwen2.5:7b",
        provider="ollama",
        input_tokens=11,
        output_tokens=22,
    )
    provider = _mock_provider_returning(resp)
    with patch(
        "src.regeneration.response_synthesizer.get_provider",
        return_value=provider,
    ):
        syn = ResponseSynthesizer(RegenSettings())
        out = await syn.synthesize(_assembled())
    assert out is resp
    assert out.text == "the-text"
    assert out.input_tokens == 11
    assert out.output_tokens == 22
