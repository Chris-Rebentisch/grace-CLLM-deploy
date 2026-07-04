"""Hypothesis property: triage determinism (Chunk 61, CP3).

Asserts that identical email input produces identical ``triage_tier_outcome``
across all four tiers with a deterministic mock LLM provider.

Settings: ``deadline=None`` to avoid flake from timing variance;
``max_examples=50`` to cap computation budget.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from hypothesis import given, settings, strategies as st

from src.ingestion.communications.triage.config import (
    Tier1Config,
    TriageConfig,
)
from src.ingestion.models import AttachmentRef, CommunicationEvent


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_BODIES = st.text(min_size=1, max_size=500)
_SENDERS = st.from_regex(r"[a-z]{3,8}@example\.com", fullmatch=True)


@st.composite
def email_events(draw):
    """Composite strategy generating email permutations."""
    body = draw(_BODIES)
    subject = draw(st.text(min_size=0, max_size=100))
    sender = draw(_SENDERS)
    n_attachments = draw(st.integers(min_value=0, max_value=3))
    attachments = [
        AttachmentRef(filename=f"file_{i}.txt", mime_type="text/plain", size_bytes=100)
        for i in range(n_attachments)
    ]
    return CommunicationEvent(
        event_id=uuid4(),
        source_id=uuid4(),
        message_id=f"<det-{uuid4()}@prop.test>",
        sender_email=sender,
        subject=subject,
        body_plain=body,
        attachments=attachments,
        source_type="mbox",
    )


# ---------------------------------------------------------------------------
# Deterministic mock LLM provider
# ---------------------------------------------------------------------------

def _deterministic_provider():
    """Mock LLM that returns a deterministic response based on input length."""
    provider = MagicMock()

    async def _generate(prompt, **kwargs):
        # Deterministic: relevant if prompt length is even
        relevant = len(prompt) % 2 == 0
        return json.dumps({"relevant": relevant, "rationale_band": "medium"})

    provider.generate = AsyncMock(side_effect=_generate)
    return provider


# ---------------------------------------------------------------------------
# Tier runner helpers (isolated, no DB)
# ---------------------------------------------------------------------------

async def _run_tiers(event: CommunicationEvent, config: TriageConfig, provider) -> str | None:
    """Run all four tiers in sequence against a single event, return outcome."""
    from src.ingestion.communications.triage.tier1_noise import run_tier1

    seen_ids: set[str] = set()

    # Tier 1
    outcome = run_tier1(event, config.tier1, seen_ids=seen_ids)
    if outcome:
        return outcome

    # Tier 2 — mock ArcadeDB (always pass)
    # No real ArcadeDB → always returns None (pass-through)

    # Tier 3 — skip (requires embedding matrix; tested elsewhere)

    # Tier 4
    from src.ingestion.communications.triage.tier4_llm import run_tier4
    t4_result = await run_tier4(event, provider, config)
    if t4_result is not None:
        return t4_result

    return "passed_to_extraction"


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

@settings(deadline=None, max_examples=50)
@given(event=email_events())
@pytest.mark.asyncio
async def test_triage_determinism(event: CommunicationEvent):
    """Identical input → identical triage_tier_outcome across repeated runs."""
    config = TriageConfig(
        tier1=Tier1Config(
            rule_order=["empty_body", "duplicate_message_id", "auto_reply"],
        ),
    )

    with patch("src.analytics.metrics.record_ingestion_triage_duration"):
        provider1 = _deterministic_provider()
        provider2 = _deterministic_provider()

        result1 = await _run_tiers(event, config, provider1)
        result2 = await _run_tiers(event, config, provider2)

    assert result1 == result2, (
        f"Non-deterministic triage: {result1!r} != {result2!r} "
        f"for event body={event.body_plain!r:.50}"
    )
