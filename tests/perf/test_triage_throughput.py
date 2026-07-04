"""Performance test: Tier 1+2+3 triage throughput (Chunk 61, CP6).

Hard-fail CI gate: >=1000 emails/min single-worker for deterministic tiers.
Plain ``time.perf_counter()`` assertions — no LLM dependency.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.perf.conftest import perf_timer
from src.ingestion.communications.triage.tier1_noise import run_tier1
from src.ingestion.communications.triage.config import Tier1Config
from src.ingestion.models import CommunicationEvent


def _make_events(n: int) -> list[CommunicationEvent]:
    """Generate N synthetic email events for throughput measurement."""
    events = []
    for i in range(n):
        events.append(
            CommunicationEvent(
                event_id=uuid4(),
                source_id=uuid4(),
                message_id=f"<perf-{i}-{uuid4()}@bench.test>",
                sender_email=f"user{i}@company.com",
                sender_display_name=f"User {i}",
                recipients=[],
                subject=f"Meeting follow-up #{i}",
                body_plain=f"This is a test email body for performance measurement. "
                           f"It contains enough text to be realistic. Message {i}.",
                body_html=None,
                sent_at=None,
                received_at=None,
                attachments=[],
                in_reply_to=None,
                references=[],
                thread_id=None,
                triage_tier_outcome="pending",
                ontology_module=None,
                raw_headers=None,
                source_type="mbox",
            )
        )
    return events


@pytest.mark.perf
def test_tier1_throughput_floor():
    """Tier 1 deterministic triage must process >=1000 emails/min.

    Hard-fail gate — no LLM dependency.
    """
    n = 1000
    events = _make_events(n)
    config = Tier1Config(
        rule_order=[
            "empty_body",
            "duplicate_message_id",
            "auto_reply",
            "newsletter",
            "calendar_invite",
            "bounce",
            "system_notification",
        ],
    )
    seen_ids: set[str] = set()

    with perf_timer() as t:
        for event in events:
            run_tier1(event, config, seen_ids=seen_ids)

    elapsed = t["elapsed"]
    rate_per_min = (n / elapsed) * 60

    assert rate_per_min >= 1000, (
        f"Tier 1 throughput {rate_per_min:.0f} emails/min < 1000 floor "
        f"(processed {n} in {elapsed:.2f}s)"
    )
