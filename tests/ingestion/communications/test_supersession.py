"""Tests for supersession.py (Chunk 80a, D514).

Unit tests for final-message contradiction detection and supersession logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.ingestion.communications.supersession import (
    _classify_value_change,
    apply_thread_supersession,
)


def test_contradiction_supersedes():
    """Earlier fact contradicted by later gets superseded (valid_to + superseded_by).

    F-0032(b): both vertices carry the SAME identity name — supersession only
    fires within a single entity identity.
    """
    thread_entities = [
        {
            "grace_id": "entity-1",
            "entity_type": "Insurance_Policy",
            "entity_name": "Policy HW-449",
            "properties": {"coverage_amount": "50000"},
            "source_message_id": "<msg1@ex.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        },
        {
            "grace_id": "entity-2",
            "entity_type": "Insurance_Policy",
            "entity_name": "Policy HW-449",
            "properties": {"coverage_amount": "75000"},
            "source_message_id": "<msg2@ex.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        },
    ]

    result = apply_thread_supersession(
        thread_id="<msg1@ex.com>",
        thread_entities=thread_entities,
    )

    assert result["superseded_count"] == 1
    assert result["preserved_count"] == 0


def test_refinement_preserved():
    """Added detail without contradiction does NOT trigger supersession.

    F-0032(b): same identity name on both vertices — the refinement is on a
    non-identity property within one entity identity.
    """
    thread_entities = [
        {
            "grace_id": "entity-1",
            "entity_type": "Legal_Entity",
            "entity_name": "Acme Corporation",
            "properties": {"address": "12 Main St"},
            "source_message_id": "<msg1@ex.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        },
        {
            "grace_id": "entity-2",
            "entity_type": "Legal_Entity",
            "entity_name": "Acme Corporation",
            "properties": {"address": "12 Main St, Springfield"},
            "source_message_id": "<msg2@ex.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        },
    ]

    result = apply_thread_supersession(
        thread_id="<msg1@ex.com>",
        thread_entities=thread_entities,
    )

    # "12 Main St" is contained in "12 Main St, Springfield" → refinement
    assert result["superseded_count"] == 0
    assert result["preserved_count"] >= 1


def test_ambiguous_emits_warning():
    """Ambiguous case (later contained in earlier) counts as ambiguous (D101 WARNING)."""
    # "Senior Manager" → "Manager" — later is contained in earlier, ambiguous
    thread_entities = [
        {
            "grace_id": "entity-1",
            "entity_type": "Person",
            "entity_name": "Alice Smith",
            "properties": {"role": "Senior Manager"},
            "source_message_id": "<msg1@ex.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        },
        {
            "grace_id": "entity-2",
            "entity_type": "Person",
            "entity_name": "Alice Smith",
            "properties": {"role": "Manager"},
            "source_message_id": "<msg2@ex.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        },
    ]

    result = apply_thread_supersession(
        thread_id="<msg1@ex.com>",
        thread_entities=thread_entities,
    )

    assert result["ambiguous_count"] == 1
    assert result["superseded_count"] == 0


def test_supersession_integration_valid_to_close():
    """Supersession returns correct counts for a clear contradiction."""
    thread_entities = [
        {
            "grace_id": "old-entity-123",
            "entity_type": "Insurance_Claim",
            "entity_name": "Claim 2026-0042",
            "properties": {"status": "open"},
            "source_message_id": "<claim1@ins.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        },
        {
            "grace_id": "new-entity-456",
            "entity_type": "Insurance_Claim",
            "entity_name": "Claim 2026-0042",
            "properties": {"status": "closed"},
            "source_message_id": "<claim2@ins.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc),
        },
    ]

    result = apply_thread_supersession(
        thread_id="<claim1@ins.com>",
        thread_entities=thread_entities,
    )

    # "open" vs "closed" — neither contains the other → contradiction
    assert result["superseded_count"] == 1
    assert result["preserved_count"] == 0
    assert result["ambiguous_count"] == 0


def test_supersession_arcade_write_sets_valid_to_and_superseded_by():
    """CP7 — mocked ArcadeDB client receives valid_to + superseded_by Cypher SET."""
    captured_queries: list[str] = []

    class _MockArcadeClient:
        async def execute_cypher(self, query: str) -> dict:
            captured_queries.append(query)
            return {"result": []}

    thread_entities = [
        {
            "grace_id": "old-entity-123",
            "entity_type": "Insurance_Claim",
            "entity_name": "Claim 2026-0042",
            "properties": {"status": "open"},
            "source_message_id": "<claim1@ins.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        },
        {
            "grace_id": "new-entity-456",
            "entity_type": "Insurance_Claim",
            "entity_name": "Claim 2026-0042",
            "properties": {"status": "closed"},
            "source_message_id": "<claim2@ins.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc),
        },
    ]

    apply_thread_supersession(
        thread_id="<claim1@ins.com>",
        thread_entities=thread_entities,
        arcade_client=_MockArcadeClient(),
    )

    assert len(captured_queries) == 1
    assert "superseded_by" in captured_queries[0]
    assert "valid_to" in captured_queries[0]
    assert "new-entity-456" in captured_queries[0]
    assert "old-entity-123" in captured_queries[0]
