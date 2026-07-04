"""Chunk 59 CP8 — write_batch evidence_origin vertex property (spec §9.1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.extraction.claim_models import Claim, ClaimStatus
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionBatch
from src.extraction.graph_writer import write_batch
from src.graph.entity_models import EntityCreateResponse


def _mock_config() -> ExtractionSettings:
    return ExtractionSettings(
        extraction_base_url="http://localhost:11434",
        database_url="postgresql://localhost/test",
    )


def _entity_claim(**kw) -> Claim:
    return Claim(
        entity_type="Legal_Entity",
        subject_name="Acme Corp",
        subject_type="Legal_Entity",
        predicate="entity",
        properties_json={"name": "Acme Corp"},
        confidence=0.85,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        resolved_entity_grace_id=None,
        **kw,
    )


def _batch(claims: list[Claim]) -> ExtractionBatch:
    return ExtractionBatch(
        document_id="doc-1",
        claims=claims,
        entities=[],
        relationships=[],
        claims_accepted=len([c for c in claims if c.status == ClaimStatus.AUTO_ACCEPTED]),
        claims_quarantined=0,
    )


SCHEMA = {
    "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
    "relationships": {},
}


@pytest.mark.asyncio
async def test_write_batch_evidence_origin_default_document() -> None:
    """write_batch defaults evidence_origin to document on new EntityCreate."""
    claim = _entity_claim()
    batch = _batch([claim])
    with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
         patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
         patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
         patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
         patch("src.extraction.graph_writer.update_event_status_after_write"):
        mock_insert.return_value = EntityCreateResponse(
            grace_id="new-gid",
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        )
        await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

    entity_arg = mock_insert.call_args[0][1]
    assert entity_arg.evidence_origin == "document"


@pytest.mark.asyncio
async def test_write_batch_evidence_origin_communication() -> None:
    """Caller-supplied communication origin reaches EntityCreate."""
    claim = _entity_claim()
    batch = _batch([claim])
    with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
         patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
         patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
         patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
         patch("src.extraction.graph_writer.update_event_status_after_write"):
        mock_insert.return_value = EntityCreateResponse(
            grace_id="new-gid",
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        )
        await write_batch(
            batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config(),
            evidence_origin="communication",
        )

    entity_arg = mock_insert.call_args[0][1]
    assert entity_arg.evidence_origin == "communication"


@pytest.mark.asyncio
async def test_write_batch_evidence_origin_hybrid_and_legacy_read_posture() -> None:
    """Hybrid enum is accepted on write; DR layer documents COALESCE read posture."""
    claim = _entity_claim()
    batch = _batch([claim])
    with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
         patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
         patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
         patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
         patch("src.extraction.graph_writer.update_event_status_after_write"):
        mock_insert.return_value = EntityCreateResponse(
            grace_id="new-gid",
            rid="#1:0",
            entity_type="Legal_Entity",
            created=True,
            canonical_match=False,
        )
        await write_batch(
            batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config(),
            evidence_origin="hybrid",
        )

    entity_arg = mock_insert.call_args[0][1]
    assert entity_arg.evidence_origin == "hybrid"

    dr_src = Path(__file__).resolve().parents[2] / "src" / "analytics" / "documented_reality.py"
    text = dr_src.read_text(encoding="utf-8")
    assert "COALESCE(evidence_origin, 'document')" in text
