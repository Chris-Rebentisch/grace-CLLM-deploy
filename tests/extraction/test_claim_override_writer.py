"""Tests for the per-claim writer (D230, Chunk 30).

Two tests cover the spec's protocol-compliance assertions:

1. **D106 idempotency preservation** — replaying ``promote_claim_to_graph``
   for the same accepted claim does NOT double-insert into ArcadeDB.
   ``entity_ops.insert_entity`` performs canonical dedup, so the second
   call returns the existing grace_id with ``created=False``.

2. **Parent extraction event status unchanged** — the per-claim writer
   touches PostgreSQL bookkeeping for the claim row only; the parent
   ``extraction_events_pg.status`` row is untouched (proves the writer
   does NOT call ``graph_writer.write_batch``, which is the only path
   that flips parent event status).

The arcade client is mocked because Chunk 30 tests run without a live
ArcadeDB; the per-claim writer's contract is verified at the
``insert_entity`` / ``insert_relationship`` boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from src.extraction.claim_database import (
    insert_claim,
    insert_extraction_event,
)
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.extraction.claim_override_writer import (
    mark_claim_rejected,
    promote_claim_to_graph,
)
from src.graph.entity_models import EntityCreateResponse


# --- Helpers ---------------------------------------------------------------


def _make_quarantined_entity_claim(extraction_event_id: str) -> Claim:
    """Build a quarantined entity claim ready for human override."""
    return Claim(
        claim_id=str(uuid4()),
        extraction_unit_id=f"unit-{uuid4().hex[:12]}",
        entity_type="Legal_Entity",
        relationship_type=None,
        subject_name="Acme Corp (override-test)",
        predicate="entity",
        object_name=None,
        properties_json={"jurisdiction": "Delaware"},
        verdict=ClaimVerdict.REFUTED,
        status=ClaimStatus.QUARANTINED,
        decision_source="verifier",
        source_document_id="doc-override-001",
        source_chunk_id="chunk-override-001",
        ontology_module="core",
        schema_version=1,
        extraction_event_id=extraction_event_id,
        created_at=datetime.now(UTC),
    )


def _seed_event_and_quarantined_claim(db_session) -> tuple[Claim, str]:
    """Insert a parent extraction event in graph_written status and a child claim."""
    event_id = str(uuid4())
    event = {
        "event_id": event_id,
        "batch_id": str(uuid4()),
        "source_document_id": "doc-override-001",
        "ontology_module": "core",
        "schema_version": 1,
        "provider_used": "ollama",
        "model_used": "qwen2.5:7b",
        "chunks_total": 1,
        "chunks_succeeded": 1,
        "chunks_failed": 0,
        "entities_extracted": 1,
        "relationships_extracted": 0,
        "claims_accepted": 0,
        "claims_quarantined": 1,
        "avg_confidence": 0.55,
        "started_at": datetime.now(UTC),
        "completed_at": datetime.now(UTC),
        "status": "graph_written",
    }
    insert_extraction_event(db_session, event)
    claim = _make_quarantined_entity_claim(event_id)
    insert_claim(db_session, claim)
    return claim, event_id


def _fetch_event_status(db_session, event_id: str) -> str:
    row = db_session.execute(
        text("SELECT status FROM extraction_events_pg WHERE event_id = :eid"),
        {"eid": UUID(event_id)},
    ).first()
    assert row is not None
    return row.status


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_claim_idempotent_replay_does_not_double_insert(
    clean_extraction_tables,
):
    """D106 carry-over: replaying accept reuses canonical entity (no double-insert)."""
    db = clean_extraction_tables
    claim, _ = _seed_event_and_quarantined_claim(db)

    new_grace_id = str(uuid4())

    # First call: insert_entity returns created=True with a fresh grace_id.
    # Second call: insert_entity returns the same grace_id with created=False
    # (canonical dedup hit). Per-claim writer must record this as a "matched"
    # outcome, not a duplicate insert.
    fake_responses = [
        EntityCreateResponse(
            grace_id=new_grace_id,
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        ),
        EntityCreateResponse(
            grace_id=new_grace_id,
            rid="#1:0",
            entity_type="Legal_Entity",
            created=False,
            canonical_match=True,
        ),
    ]

    arcade_client = object()  # placeholder; insert_entity is patched

    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(side_effect=fake_responses),
    ) as patched_insert:
        first = await promote_claim_to_graph(
            claim=claim,
            reviewer="alice",
            notes="ratified by SME",
            session=db,
            arcade_client=arcade_client,
        )
        # Replay the same accept call (D106 preservation contract).
        second = await promote_claim_to_graph(
            claim=claim,
            reviewer="alice",
            notes="ratified by SME (replay)",
            session=db,
            arcade_client=arcade_client,
        )

    # Two calls; never more — the per-claim writer must NOT loop or retry.
    assert patched_insert.await_count == 2, "insert_entity called too many times"

    # First call counts as create; second counts as canonical match.
    assert first["entities_created"] == 1
    assert first["entities_matched"] == 0
    assert second["entities_created"] == 0
    assert second["entities_matched"] == 1

    # Status flipped to AUTO_ACCEPTED with human decision source.
    row = db.execute(
        text(
            "SELECT status, decision_source, human_decided_at "
            "FROM extraction_claims WHERE claim_id = :cid"
        ),
        {"cid": UUID(claim.claim_id)},
    ).first()
    assert row.status == ClaimStatus.AUTO_ACCEPTED.value
    assert row.decision_source == "human"
    assert row.human_decided_at is not None


@pytest.mark.asyncio
async def test_promote_claim_does_not_touch_parent_event_status(
    clean_extraction_tables,
):
    """Per-claim writer leaves extraction_events_pg.status untouched (D106 boundary)."""
    db = clean_extraction_tables
    claim, event_id = _seed_event_and_quarantined_claim(db)
    assert _fetch_event_status(db, event_id) == "graph_written"

    arcade_client = object()
    fake_resp = EntityCreateResponse(
        grace_id=str(uuid4()),
        rid="#1:1",
        entity_type="Legal_Entity",
        created=True,
        canonical_match=False,
    )

    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(return_value=fake_resp),
    ):
        await promote_claim_to_graph(
            claim=claim,
            reviewer="bob",
            notes=None,
            session=db,
            arcade_client=arcade_client,
        )

    # Parent extraction event status MUST NOT change after a per-claim
    # promotion — the writer is forbidden from invoking write_batch
    # (which is the only path that flips this flag).
    assert _fetch_event_status(db, event_id) == "graph_written"

    # Reject path is purely PostgreSQL — verify it also doesn't touch
    # the parent event.
    other_claim = _make_quarantined_entity_claim(event_id)
    insert_claim(db, other_claim)
    mark_claim_rejected(claim=other_claim, reviewer="bob", notes=None, session=db)
    assert _fetch_event_status(db, event_id) == "graph_written"
    rejected_row = db.execute(
        text(
            "SELECT status, decision_source, human_decided_at "
            "FROM extraction_claims WHERE claim_id = :cid"
        ),
        {"cid": UUID(other_claim.claim_id)},
    ).first()
    assert rejected_row.status == ClaimStatus.REJECTED.value
    assert rejected_row.decision_source == "human"
    assert rejected_row.human_decided_at is not None
