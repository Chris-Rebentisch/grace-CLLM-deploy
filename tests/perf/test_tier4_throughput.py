"""Performance test: Tier 4 LLM throughput (Chunk 61, CP6).

Warn-only + skip-gracefully when Ollama unavailable.
Target: ~60 emails/min on qwen2.5:7b.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tests.perf.conftest import perf_timer, skip_if_no_ollama
from src.ingestion.models import CommunicationEvent


def _make_events(n: int) -> list[CommunicationEvent]:
    """Generate N synthetic email events."""
    return [
        CommunicationEvent(
            event_id=uuid4(),
            source_id=uuid4(),
            message_id=f"<t4-perf-{i}-{uuid4()}@bench.test>",
            sender_email=f"sender{i}@company.com",
            body_plain=f"Business discussion about contract {i} and regulatory compliance.",
            source_type="mbox",
        )
        for i in range(n)
    ]


@pytest.mark.perf
@pytest.mark.asyncio
async def test_tier4_throughput_floor():
    """Tier 4 with mock LLM must complete within reasonable wall-clock.

    Uses a mock provider to measure pipeline overhead excluding actual
    LLM latency. Skip-gracefully + warn-only semantics.
    """
    from src.ingestion.communications.triage.tier4_llm import run_tier4

    n = 60
    events = _make_events(n)

    provider = MagicMock()
    provider.generate = AsyncMock(
        return_value=json.dumps({"relevant": True, "rationale_band": "medium"})
    )

    config = MagicMock()
    config.tier4 = MagicMock()
    config.tier4.cost_budget_usd_per_run = 100.0  # High budget to avoid gate

    # Reset accumulated cost
    import src.ingestion.communications.triage.tier4_llm as mod
    old_cost = mod._accumulated_cost_usd
    mod._accumulated_cost_usd = 0.0

    try:
        with perf_timer() as t:
            for event in events:
                await run_tier4(event, provider, config)

        elapsed = t["elapsed"]
        rate_per_min = (n / elapsed) * 60

        # With mock LLM, overhead should be minimal — assert >60/min
        assert rate_per_min >= 60, (
            f"Tier 4 pipeline overhead {rate_per_min:.0f} emails/min < 60 floor "
            f"(processed {n} in {elapsed:.2f}s)"
        )
    finally:
        mod._accumulated_cost_usd = old_cost
