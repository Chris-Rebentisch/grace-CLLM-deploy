"""F-29 regression: thread supersession must exclude system vertices + `name`.

validation run: thread supersession swept Extraction_Event provenance
vertices into the thread-entity set and treated system properties like `name`
as supersedable facts. Only Document_Chunk was excluded. The fix excludes ALL
system vertex types and adds `name` (+ other identity/provenance fields) to the
property denylist.
"""

from __future__ import annotations

import asyncio

import pytest

from src.ingestion.communications import supersession
from src.ingestion.communications.supersession import (
    _SYSTEM_VERTEX_TYPES,
    _VERTEX_SYSTEM_NAMES,
    fetch_thread_entities_from_arcade,
)


def test_f29_system_vertex_types_are_excluded_set():
    """The exclusion set must contain all system vertex types, not just Document_Chunk."""
    for t in ("Extraction_Event", "Query_Event", "Response_Event", "Image_Asset", "Document_Chunk"):
        assert t in _SYSTEM_VERTEX_TYPES


def test_f29_name_is_denylisted_property():
    """`name` (a vertex identity label) must not be a supersedable property."""
    assert "name" in _VERTEX_SYSTEM_NAMES


class _FakeArcadeClient:
    def __init__(self, rows_by_doc: dict[str, list[dict]]):
        self._rows_by_doc = rows_by_doc
        self.queries: list[str] = []

    async def execute_cypher(self, query: str) -> dict:
        self.queries.append(query)
        # Return rows for whichever doc id appears in the query.
        for doc_id, rows in self._rows_by_doc.items():
            if doc_id in query:
                return {"result": rows}
        return {"result": []}


def test_f29_extraction_event_vertex_excluded_from_thread_entities():
    """A system Extraction_Event vertex must NOT be returned as a thread entity."""
    members = [{"message_id": "<m1@x.com>", "thread_position": 0, "sent_at": None}]
    rows = {
        "email:<m1@x.com>": [
            # System provenance vertex — must be dropped.
            {"n": {"@type": "Extraction_Event", "grace_id": "ev-1", "name": "extract-run-1"}},
            # A real domain entity — must be kept.
            {"n": {"@type": "Insurance_Claim", "grace_id": "claim-1", "status": "open"}},
        ]
    }
    client = _FakeArcadeClient(rows)
    result = asyncio.run(fetch_thread_entities_from_arcade(client, members))

    types = {e["entity_type"] for e in result}
    assert "Extraction_Event" not in types
    assert "Insurance_Claim" in types


def test_f29_name_not_in_returned_domain_properties():
    """Even for a kept domain vertex, `name` must be stripped from supersedable props."""
    members = [{"message_id": "<m2@x.com>", "thread_position": 0, "sent_at": None}]
    rows = {
        "email:<m2@x.com>": [
            {"n": {"@type": "Person", "grace_id": "p-1", "name": "Alice", "title": "Partner"}},
        ]
    }
    client = _FakeArcadeClient(rows)
    result = asyncio.run(fetch_thread_entities_from_arcade(client, members))

    assert len(result) == 1
    props = result[0]["properties"]
    assert "name" not in props
    assert props.get("title") == "Partner"  # real domain prop kept
