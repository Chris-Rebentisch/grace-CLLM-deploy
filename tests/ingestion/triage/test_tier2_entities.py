"""Tier 2 entity lookup tests (Chunk 56 CP3 — 7 tests)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.ingestion.communications.triage.tier2_entities import (
    _PERSON_QUERY,
    _ORG_QUERY,
    _extract_sender_name,
    run_tier2,
)
from src.ingestion.models import CommunicationEvent


def _make_event(**overrides) -> CommunicationEvent:
    defaults = dict(
        source_id=uuid4(),
        message_id=f"<{uuid4()}@example.com>",
        sender_email="alice@example.com",
        body_plain="Test body",
        source_type="mbox",
    )
    defaults.update(overrides)
    return CommunicationEvent(**defaults)


@pytest.mark.asyncio
async def test_person_match_returns_none():
    """Person match → pass to next tier (None)."""
    ev = _make_event(sender_display_name="Alice Example")
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"grace_id": "abc"}]},  # Person match
        {"result": []},  # Org no match
    ])
    assert await run_tier2(ev, client) is None


@pytest.mark.asyncio
async def test_org_match_returns_none():
    """Organization match → pass to next tier (None)."""
    ev = _make_event(sender_display_name="Acme Corp")
    client = AsyncMock()
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": []},  # Person no match
        {"result": [{"grace_id": "xyz"}]},  # Org match
    ])
    assert await run_tier2(ev, client) is None


@pytest.mark.asyncio
async def test_no_match_returns_filtered():
    """Neither match → filtered."""
    ev = _make_event(sender_display_name="Unknown Sender")
    client = AsyncMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})
    assert await run_tier2(ev, client) == "filtered_t2_no_known_entity"


@pytest.mark.asyncio
async def test_aliases_match_returns_none():
    """Match via aliases → pass."""
    ev = _make_event(sender_display_name="Ali")
    client = AsyncMock()
    # The query checks aliases; mock returns match
    client.execute_cypher = AsyncMock(side_effect=[
        {"result": [{"grace_id": "match-via-alias"}]},
        {"result": []},
    ])
    assert await run_tier2(ev, client) is None


def test_sender_display_name_extraction():
    """Display name preferred over email local part."""
    ev = _make_event(sender_display_name="Bob Smith", sender_email="bob@corp.com")
    assert _extract_sender_name(ev) == "Bob Smith"


def test_sender_email_fallback():
    """Falls back to email local part when no display name."""
    ev = _make_event(sender_display_name=None, sender_email="charlie@corp.com")
    assert _extract_sender_name(ev) == "charlie"


def test_query_strings_negative_assertion():
    """Queries do NOT reference canonical_name or email_addresses (D430)."""
    for query in (_PERSON_QUERY, _ORG_QUERY):
        assert "canonical_name" not in query
        assert "email_addresses" not in query
        # No f-string interpolation — no curly braces with variables
        assert "{sender" not in query
