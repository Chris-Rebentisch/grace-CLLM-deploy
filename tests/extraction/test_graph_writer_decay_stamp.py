"""Tests for decay-eligibility stamping at graph write time (F-0044 / ISS-0033).

The confidence-decay batch (src/extraction/confidence_decay.py) requires
`last_verified_at` + `confidence_at_verification` + `verdict` all non-null on
a vertex/edge to consider it. The graph writer previously stamped none of
them, so fresh-graph decay coverage was ~0%. These tests assert:

* new-vertex writes carry the triple (via EntityCreate.properties);
* new-edge writes carry the triple (via RelationshipCreate.properties);
* the ER-resolved merge path fill-only stamps a vertex that lacks the
  triple, and does NOT clobber a vertex that already carries it
  (e.g. a D452 review-stamped vertex keeps its verification epoch);
* the verdict is stamped verbatim (PENDING is never upgraded).

Pure unit tests — arcade client and entity/relationship ops are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionBatch
from src.extraction.graph_writer import _decay_eligibility_stamps, write_batch
from src.graph.entity_models import EntityCreateResponse


SCHEMA = {
    "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
    "relationships": {"owns": {"source_type": "Legal_Entity", "target_type": "Legal_Entity"}},
}

DECAY_TRIPLE = ("last_verified_at", "confidence_at_verification", "verdict")


def _config() -> ExtractionSettings:
    return ExtractionSettings(
        extraction_base_url="http://localhost:11434",
        database_url="postgresql://localhost/test",
    )


def _entity_claim(name="Acme Corp", resolved_gid=None, verdict=ClaimVerdict.SUPPORTED, confidence=0.85) -> Claim:
    return Claim(
        entity_type="Legal_Entity",
        subject_name=name,
        subject_type="Legal_Entity",
        predicate="entity",
        properties_json={"name": name},
        confidence=confidence,
        verdict=verdict,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        resolved_entity_grace_id=resolved_gid,
        status=ClaimStatus.AUTO_ACCEPTED,
    )


def _rel_claim(subj="Acme Corp", obj="Sub Corp", verdict=ClaimVerdict.SUPPORTED) -> Claim:
    return Claim(
        relationship_type="owns",
        subject_name=subj,
        subject_type="Legal_Entity",
        predicate="owns",
        object_name=obj,
        object_type="Legal_Entity",
        properties_json={},
        confidence=0.85,
        verdict=verdict,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        status=ClaimStatus.AUTO_ACCEPTED,
    )


def _batch(claims) -> ExtractionBatch:
    return ExtractionBatch(
        document_id="doc-1",
        claims=claims,
        entities=[],
        relationships=[],
        claims_accepted=len(claims),
        claims_quarantined=0,
    )


def _write_patches():
    """Common patch set for write_batch unit tests (no services)."""
    return (
        patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}),
        patch("src.extraction.graph_writer.embed_texts", new_callable=AsyncMock, return_value=[[0.0] * 8]),
        patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"),
        patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1),
        patch("src.extraction.graph_writer.update_event_status_after_write"),
    )


class TestDecayEligibilityStampsHelper:
    def test_full_triple_present(self):
        stamps = _decay_eligibility_stamps(_entity_claim())
        assert set(DECAY_TRIPLE) <= set(stamps)
        assert stamps["confidence_at_verification"] == 0.85
        assert stamps["verdict"] == "SUPPORTED"
        # ISO-8601 with UTC offset — parseable by confidence_decay._parse_iso
        from src.extraction.confidence_decay import _parse_iso
        assert _parse_iso(stamps["last_verified_at"]) is not None

    def test_verdict_stamped_verbatim_never_upgraded(self):
        """PENDING must be stamped as PENDING — honesty over coverage-boosting."""
        stamps = _decay_eligibility_stamps(_entity_claim(verdict=ClaimVerdict.PENDING))
        assert stamps["verdict"] == "PENDING"

    def test_missing_confidence_omitted_not_null(self):
        """Never write nulls — omission leaves the row honestly decay-ineligible."""
        stamps = _decay_eligibility_stamps(_entity_claim(confidence=None))
        assert "confidence_at_verification" not in stamps
        assert "last_verified_at" in stamps


@pytest.mark.asyncio
class TestNewVertexStamp:
    async def test_new_entity_write_carries_triple(self):
        claim = _entity_claim()
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = EntityCreateResponse(
                grace_id="new-gid", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )
            result = await write_batch(
                _batch([claim]), SCHEMA, AsyncMock(), MagicMock(), "evt-1", _config(),
            )

        assert result.entities_created == 1
        entity_create = mock_insert.call_args.args[1]
        props = entity_create.properties
        for key in DECAY_TRIPLE:
            assert key in props, f"decay-eligibility property {key!r} not stamped"
        assert props["confidence_at_verification"] == 0.85
        assert props["verdict"] == "SUPPORTED"

    async def test_pending_verdict_stamped_verbatim_on_vertex(self):
        claim = _entity_claim(verdict=ClaimVerdict.PENDING)
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = EntityCreateResponse(
                grace_id="new-gid", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )
            await write_batch(
                _batch([claim]), SCHEMA, AsyncMock(), MagicMock(), "evt-1", _config(),
            )
        props = mock_insert.call_args.args[1].properties
        assert props["verdict"] == "PENDING"


@pytest.mark.asyncio
class TestMergePathFillOnly:
    """ER-resolved path: stamp only what the existing vertex lacks."""

    async def test_unstamped_existing_vertex_gets_fill_only_set(self):
        claim = _entity_claim(resolved_gid="existing-gid")
        existing = {
            "grace_id": "existing-gid",
            "name": "Acme Corp",
            "evidence_origin": "document",
            "sensitivity_tags": "",
            # no decay triple — pre-F-0044 vertex
        }
        client = AsyncMock()
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.get_entity", new_callable=AsyncMock, return_value=existing):
            result = await write_batch(
                _batch([claim]), SCHEMA, client, MagicMock(), "evt-1", _config(),
            )

        assert result.entities_matched == 1
        stamp_queries = [
            c.args[0] for c in client.execute_cypher.call_args_list
            if "last_verified_at" in c.args[0]
        ]
        assert len(stamp_queries) == 1, "expected exactly one fill-only stamp SET"
        q = stamp_queries[0]
        assert "existing-gid" in q
        assert "confidence_at_verification" in q
        assert "verdict" in q

    async def test_already_stamped_vertex_not_clobbered(self):
        """A D452 review-stamped vertex keeps its verification epoch."""
        claim = _entity_claim(resolved_gid="existing-gid")
        existing = {
            "grace_id": "existing-gid",
            "name": "Acme Corp",
            "evidence_origin": "document",
            "sensitivity_tags": "",
            "last_verified_at": "2026-01-01T00:00:00+00:00",
            "confidence_at_verification": 0.9,
            "verdict": "SUPPORTED",
        }
        client = AsyncMock()
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.get_entity", new_callable=AsyncMock, return_value=existing):
            result = await write_batch(
                _batch([claim]), SCHEMA, client, MagicMock(), "evt-1", _config(),
            )

        assert result.entities_matched == 1
        for call in client.execute_cypher.call_args_list:
            assert "last_verified_at" not in call.args[0], (
                "already-stamped vertex must not receive a decay-stamp SET"
            )

    async def test_partially_stamped_vertex_fills_only_missing_members(self):
        claim = _entity_claim(resolved_gid="existing-gid")
        existing = {
            "grace_id": "existing-gid",
            "name": "Acme Corp",
            "evidence_origin": "document",
            "sensitivity_tags": "",
            "verdict": "SUPPORTED",  # only one member present
        }
        client = AsyncMock()
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.get_entity", new_callable=AsyncMock, return_value=existing):
            await write_batch(
                _batch([claim]), SCHEMA, client, MagicMock(), "evt-1", _config(),
            )

        stamp_queries = [
            c.args[0] for c in client.execute_cypher.call_args_list
            if "last_verified_at" in c.args[0]
        ]
        assert len(stamp_queries) == 1
        # existing verdict must NOT be re-SET
        assert "verdict" not in stamp_queries[0]


@pytest.mark.asyncio
class TestNewEdgeStamp:
    async def test_new_relationship_write_carries_triple(self):
        acme = _entity_claim(name="Acme Corp")
        sub = _entity_claim(name="Sub Corp")
        rel = _rel_claim(subj="Acme Corp", obj="Sub Corp")
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock) as mock_rel, \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_insert.side_effect = [
                EntityCreateResponse(grace_id="gid-acme", rid="#1:0", entity_type="Legal_Entity", created=True, canonical_match=False),
                EntityCreateResponse(grace_id="gid-sub", rid="#1:1", entity_type="Legal_Entity", created=True, canonical_match=False),
            ]
            result = await write_batch(
                _batch([acme, sub, rel]), SCHEMA, AsyncMock(), MagicMock(), "evt-1", _config(),
            )

        assert result.relationships_created == 1
        rel_create = mock_rel.call_args.args[1]
        props = rel_create.properties
        for key in DECAY_TRIPLE:
            assert key in props, f"decay-eligibility property {key!r} not stamped on edge"
        assert props["confidence_at_verification"] == 0.85
        assert props["verdict"] == "SUPPORTED"

    async def test_edge_stamp_does_not_mutate_claim_properties_json(self):
        """The stamp rides a COPY — the claim's own dict stays clean."""
        acme = _entity_claim(name="Acme Corp")
        sub = _entity_claim(name="Sub Corp")
        rel = _rel_claim(subj="Acme Corp", obj="Sub Corp")
        p1, p2, p3, p4, p5 = _write_patches()
        with p1, p2, p3, p4, p5, \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock), \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_insert.side_effect = [
                EntityCreateResponse(grace_id="gid-acme", rid="#1:0", entity_type="Legal_Entity", created=True, canonical_match=False),
                EntityCreateResponse(grace_id="gid-sub", rid="#1:1", entity_type="Legal_Entity", created=True, canonical_match=False),
            ]
            await write_batch(
                _batch([acme, sub, rel]), SCHEMA, AsyncMock(), MagicMock(), "evt-1", _config(),
            )
        assert "last_verified_at" not in rel.properties_json
