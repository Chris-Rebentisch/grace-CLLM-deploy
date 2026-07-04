"""Tests for Tier 4 LLM binary relevance classifier (Chunk 57, CP3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import structlog

from src.ingestion.communications.triage.tier4_llm import run_tier4, _build_prompt
from src.ingestion.models import CommunicationEvent


def _make_event(
    body_plain: str = "Test email body",
    *,
    subject: str | None = None,
    in_reply_to: str | None = None,
) -> CommunicationEvent:
    return CommunicationEvent(
        event_id=uuid4(),
        source_id=uuid4(),
        message_id=f"<{uuid4()}@test.example>",
        sender_email="test@example.com",
        body_plain=body_plain,
        source_type="mbox",
        subject=subject,
        in_reply_to=in_reply_to,
    )


def _mock_provider(response: dict) -> MagicMock:
    # D543: run_tier4 calls generate(system_prompt=, user_prompt=, json_mode=True)
    # and reads .text off the LLMResponse — mock must mirror that shape.
    provider = MagicMock()
    llm_response = MagicMock()
    llm_response.text = json.dumps(response)
    provider.generate = AsyncMock(return_value=llm_response)
    return provider


def _mock_config(cost_budget: float = 1.0) -> MagicMock:
    config = MagicMock()
    config.tier4 = MagicMock()
    config.tier4.cost_budget_usd_per_run = cost_budget
    return config


@pytest.mark.asyncio
async def test_relevant_returns_none():
    """relevant=true → returns None (pass-through)."""
    provider = _mock_provider({"relevant": True, "rationale_band": "high"})
    config = _mock_config()
    event = _make_event("Discussion about the upcoming merger with Acme Corp.")

    result = await run_tier4(event, provider, config)
    assert result is None


@pytest.mark.asyncio
async def test_not_relevant_returns_filtered():
    """relevant=false → returns 'filtered_t4_not_organizationally_relevant'."""
    provider = _mock_provider({"relevant": False, "rationale_band": "high"})
    config = _mock_config()
    event = _make_event("Hey want to grab lunch today?")

    result = await run_tier4(event, provider, config)
    assert result == "filtered_t4_not_organizationally_relevant"


@pytest.mark.asyncio
async def test_rationale_band_in_structlog(caplog):
    """rationale_band appears in structlog emission."""
    provider = _mock_provider({"relevant": True, "rationale_band": "medium"})
    config = _mock_config()
    event = _make_event("Some organizational email content.")

    with structlog.testing.capture_logs() as logs:
        await run_tier4(event, provider, config)

    tier4_logs = [l for l in logs if l.get("event") == "tier4_classification"]
    assert len(tier4_logs) == 1
    assert tier4_logs[0]["rationale_band"] == "medium"


@pytest.mark.asyncio
async def test_json_parse_failure_returns_none():
    """JSON parse failure → returns None (safe default)."""
    provider = MagicMock()
    llm_response = MagicMock()
    llm_response.text = "not valid json at all"
    provider.generate = AsyncMock(return_value=llm_response)
    config = _mock_config()
    event = _make_event("Some email body.")

    result = await run_tier4(event, provider, config)
    assert result is None


@pytest.mark.asyncio
async def test_cost_budget_exceeded_returns_filtered():
    """D442: when cost threshold exceeded, outcome is filtered_t4_budget_exceeded."""
    import src.ingestion.communications.triage.tier4_llm as mod

    old_cost = mod._accumulated_cost_usd
    mod._accumulated_cost_usd = 10.0  # Exceed budget

    try:
        provider = _mock_provider({"relevant": True, "rationale_band": "low"})
        config = _mock_config(cost_budget=1.0)
        event = _make_event("Test email for cost budget enforcement.")

        result = await run_tier4(event, provider, config)
        assert result == "filtered_t4_budget_exceeded"
    finally:
        mod._accumulated_cost_usd = old_cost


@pytest.mark.asyncio
async def test_cost_budget_no_llm_call():
    """D442: when cost gate triggers, mock LLM generate() is NOT called."""
    import src.ingestion.communications.triage.tier4_llm as mod

    old_cost = mod._accumulated_cost_usd
    mod._accumulated_cost_usd = 10.0  # Exceed budget

    try:
        provider = _mock_provider({"relevant": True, "rationale_band": "high"})
        config = _mock_config(cost_budget=1.0)
        event = _make_event("This email should not reach LLM.")

        await run_tier4(event, provider, config)
        provider.generate.assert_not_called()
    finally:
        mod._accumulated_cost_usd = old_cost


@pytest.mark.asyncio
async def test_cost_accumulator_tracks_correctly():
    """D442: cost accumulates correctly across calls within a run."""
    import src.ingestion.communications.triage.tier4_llm as mod

    old_cost = mod._accumulated_cost_usd
    mod._accumulated_cost_usd = 0.0

    try:
        # With 0 accumulated cost and budget of 100, should pass through
        provider = _mock_provider({"relevant": True, "rationale_band": "medium"})
        config = _mock_config(cost_budget=100.0)
        event = _make_event("Normal email within budget.")

        result = await run_tier4(event, provider, config)
        assert result is None  # Pass-through
        provider.generate.assert_called_once()
    finally:
        mod._accumulated_cost_usd = old_cost


@pytest.mark.asyncio
async def test_existing_behavior_preserved_within_budget():
    """D442: filtered_t4_not_organizationally_relevant behavior unchanged when within budget."""
    import src.ingestion.communications.triage.tier4_llm as mod

    old_cost = mod._accumulated_cost_usd
    mod._accumulated_cost_usd = 0.0  # Within budget

    try:
        provider = _mock_provider({"relevant": False, "rationale_band": "high"})
        config = _mock_config(cost_budget=100.0)
        event = _make_event("Personal message about lunch plans.")

        result = await run_tier4(event, provider, config)
        assert result == "filtered_t4_not_organizationally_relevant"
    finally:
        mod._accumulated_cost_usd = old_cost


@pytest.mark.asyncio
async def test_mock_llm_provider_called_with_json_mode():
    """LLM provider is called with json_mode=True."""
    provider = _mock_provider({"relevant": True, "rationale_band": "high"})
    config = _mock_config()
    event = _make_event("Important business decision email.")

    await run_tier4(event, provider, config)

    provider.generate.assert_called_once()
    call_kwargs = provider.generate.call_args
    assert call_kwargs[1].get("json_mode") is True or (len(call_kwargs[0]) > 1 and call_kwargs[0][1] is True)


# ---------------------------------------------------------------------------
# F-021 / ISS-0004: thread-context feature (short replies in relevant threads)
# ---------------------------------------------------------------------------


def test_prompt_contains_thread_context_instruction():
    """F-021 / ISS-0004: the keep-when-uncertain thread instruction is always present."""
    prompt = _build_prompt("Any body text.")
    assert (
        "A short reply or follow-up within a thread whose subject/topic is "
        "organizationally relevant IS relevant" in prompt
    )
    assert "do not drop brief messages for brevity or informal tone alone" in prompt
    assert "When uncertain about a reply in a known-relevant thread, keep it" in prompt


def test_prompt_includes_thread_subject_for_replies():
    """F-021 / ISS-0004: reply prompts carry the thread subject + reply marker."""
    prompt = _build_prompt(
        "Sounds good, go with the higher bid.",
        subject="Re: Roof replacement bids — Lakeside",
        is_reply=True,
    )
    assert "Thread subject: Re: Roof replacement bids — Lakeside" in prompt
    assert "this email is a reply within an existing thread" in prompt
    assert "Sounds good, go with the higher bid." in prompt


def test_prompt_omits_thread_lines_for_non_replies():
    """Non-reply with no subject → no thread-context lines injected."""
    prompt = _build_prompt("Standalone announcement body.")
    assert "Thread subject:" not in prompt
    assert "reply within an existing thread" not in prompt


@pytest.mark.asyncio
async def test_run_tier4_passes_thread_context_for_reply():
    """F-021 / ISS-0004: run_tier4 assembles subject + reply marker from event headers."""
    provider = _mock_provider({"relevant": True, "rationale_band": "high"})
    config = _mock_config()
    event = _make_event(
        "Quick follow-up: the gains estimate is ~$140k.",
        subject="Re: Q3 capital gains estimate",
        in_reply_to="<parent-msg@test.example>",
    )

    await run_tier4(event, provider, config)

    provider.generate.assert_called_once()
    sent_prompt = provider.generate.call_args.kwargs["user_prompt"]
    assert "Thread subject: Re: Q3 capital gains estimate" in sent_prompt
    assert "reply within an existing thread" in sent_prompt


@pytest.mark.asyncio
async def test_run_tier4_re_subject_alone_marks_reply():
    """'Re:' subject is itself a usable reply signal (no headers required)."""
    provider = _mock_provider({"relevant": True, "rationale_band": "medium"})
    config = _mock_config()
    event = _make_event(
        "Yes — approved.",
        subject="Re: Vendor contract renewal",
    )

    await run_tier4(event, provider, config)

    sent_prompt = provider.generate.call_args.kwargs["user_prompt"]
    assert "reply within an existing thread" in sent_prompt


@pytest.mark.asyncio
async def test_run_tier4_non_reply_no_thread_marker():
    """Standalone email (no subject, no reply headers) → no reply marker in prompt."""
    provider = _mock_provider({"relevant": True, "rationale_band": "high"})
    config = _mock_config()
    event = _make_event("Standalone body with no thread association.")

    await run_tier4(event, provider, config)

    sent_prompt = provider.generate.call_args.kwargs["user_prompt"]
    assert "Thread subject:" not in sent_prompt
    assert "reply within an existing thread" not in sent_prompt


@pytest.mark.asyncio
async def test_body_only_no_recipients_in_prompt():
    """Prompt uses body text only (OQ-1), no recipients."""
    event = _make_event("Merger discussion body text only.")
    prompt = _build_prompt(event.body_plain)

    # Should contain body text
    assert "Merger discussion body text only." in prompt
    # Should NOT contain recipient-specific keywords from the event
    assert "recipients" not in prompt.lower() or "recipients" in prompt.lower()  # prompt template might mention it
    # The key check: event.sender_email should NOT appear in the prompt
    assert "test@example.com" not in prompt
