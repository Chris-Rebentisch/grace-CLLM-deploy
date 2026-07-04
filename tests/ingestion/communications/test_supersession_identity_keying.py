"""F-0032(b) / ISS-0036 regression: supersession must be keyed by ENTITY IDENTITY.

validation run: `_compute_supersession_updates` grouped claims by
``(entity_type, property_name)`` across the WHOLE thread, so two DIFFERENT
Persons with different ``full_name`` values were counted as a "contradiction"
and the earlier PERSON was wrongly marked superseded (4 Person vertices +
1 Lease in the run). Only claims about the SAME entity (identity name match;
conservative per-vertex fallback when nameless) may supersede each other.

Pure unit tests — no DB, no services, mock arcade client only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.ingestion.communications.supersession import (
    _compute_supersession_updates,
    _entity_identity_key,
    apply_thread_supersession,
    fetch_thread_entities_from_arcade,
)


def _person(grace_id: str, full_name: str, position: int, **props) -> dict:
    return {
        "grace_id": grace_id,
        "entity_type": "Person",
        "properties": {"full_name": full_name, **props},
        "source_message_id": f"<msg{position}@ex.com>",
        "thread_position": position,
        "sent_at": datetime(2026, 1, 1 + position, 10, 0, tzinfo=timezone.utc),
    }


def test_two_different_persons_are_not_a_contradiction():
    """Reproduces the run's failure: two Persons with different full_name
    must produce NO supersession updates (they are different entities,
    not a corrected fact)."""
    thread_entities = [
        _person("person-1", "Margaret Whitfield", 0),
        _person("person-2", "Daniel Brooks", 1),
    ]

    updates, result = _compute_supersession_updates("<msg0@ex.com>", thread_entities)

    assert updates == []
    assert result["superseded_count"] == 0

    # And through the public entrypoint too.
    result2 = apply_thread_supersession(
        thread_id="<msg0@ex.com>", thread_entities=thread_entities
    )
    assert result2["superseded_count"] == 0


def test_many_distinct_persons_never_supersede_each_other():
    """The run superseded 4 Person vertices — N distinct people in one
    thread must yield zero supersession updates."""
    thread_entities = [
        _person(f"person-{i}", name, i)
        for i, name in enumerate(
            ["Margaret Whitfield", "Daniel Brooks", "Susan Hale", "Tom Weaver"]
        )
    ]

    updates, result = _compute_supersession_updates("<t@ex.com>", thread_entities)

    assert updates == []
    assert result["superseded_count"] == 0


def test_same_person_contradicting_property_still_supersedes():
    """Identity keying must not kill real supersession: the SAME person
    (same full_name) with a corrected non-identity property still fires."""
    thread_entities = [
        _person("person-1", "Margaret Whitfield", 0, phone="555-0101"),
        _person("person-2", "Margaret Whitfield", 1, phone="555-0199"),
    ]

    updates, result = _compute_supersession_updates("<t@ex.com>", thread_entities)

    assert result["superseded_count"] == 1
    assert len(updates) == 1
    assert updates[0]["superseded_grace_id"] == "person-1"
    assert updates[0]["superseding_grace_id"] == "person-2"


def test_nameless_vertices_are_not_cross_superseded():
    """Conservative fallback: with NO identity-name evidence, each vertex is
    its own identity — cross-vertex supersession requires positive
    same-entity evidence."""
    thread_entities = [
        {
            "grace_id": "lease-1",
            "entity_type": "Lease",
            "properties": {"monthly_rent": "4200"},
            "source_message_id": "<m0@ex.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        {
            "grace_id": "lease-2",
            "entity_type": "Lease",
            "properties": {"monthly_rent": "3900"},
            "source_message_id": "<m1@ex.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        },
    ]

    updates, result = _compute_supersession_updates("<m0@ex.com>", thread_entities)

    assert updates == []
    assert result["superseded_count"] == 0


def test_same_grace_id_never_self_supersedes():
    """F-0032(c) guard: assertions that resolved to ONE merged vertex must
    not produce a self-referencing vertex-level supersession write."""
    thread_entities = [
        {
            "grace_id": "bid-merged",
            "entity_type": "Bid",
            "entity_name": "Roof repair bid",
            "properties": {"amount": "18500"},
            "source_message_id": "<m0@ex.com>",
            "thread_position": 0,
            "sent_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        {
            "grace_id": "bid-merged",
            "entity_type": "Bid",
            "entity_name": "Roof repair bid",
            "properties": {"amount": "15800"},
            "source_message_id": "<m1@ex.com>",
            "thread_position": 1,
            "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        },
    ]

    updates, result = _compute_supersession_updates("<m0@ex.com>", thread_entities)

    assert updates == []
    assert result["superseded_count"] == 0


def test_identity_key_prefers_entity_name_then_full_name():
    assert _entity_identity_key(
        {"grace_id": "g1", "entity_type": "Person", "entity_name": "Alice", "properties": {}}
    ) == ("Person", "name:alice")
    assert _entity_identity_key(
        {"grace_id": "g1", "entity_type": "Person", "properties": {"full_name": "Alice B"}}
    ) == ("Person", "name:alice b")
    # Nameless → per-vertex identity.
    assert _entity_identity_key(
        {"grace_id": "g9", "entity_type": "Lease", "properties": {"rent": "1"}}
    ) == ("Lease", "grace:g9")


def test_fetch_thread_entities_carries_entity_name():
    """fetch must carry the vertex `name` label separately (F-29 strips it
    from supersedable properties, but identity keying needs it)."""

    class _FakeArcadeClient:
        async def execute_cypher(self, query: str) -> dict:
            return {
                "result": [
                    {"n": {"@type": "Person", "grace_id": "p-1", "name": "Alice", "title": "Partner"}}
                ]
            }

    members = [{"message_id": "<m1@x.com>", "thread_position": 0, "sent_at": None}]
    result = asyncio.run(fetch_thread_entities_from_arcade(_FakeArcadeClient(), members))

    assert len(result) == 1
    assert result[0]["entity_name"] == "Alice"
    assert "name" not in result[0]["properties"]  # F-29 denylist still holds
