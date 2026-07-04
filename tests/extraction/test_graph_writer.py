"""Tests for graph writer — claim to ArcadeDB bridge."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.extraction.claim_models import Claim, ClaimStatus, ConstraintSeverity
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
)
from src.extraction.graph_writer import WriteResult, write_batch
from src.graph.entity_models import EntityCreateResponse


def _mock_config() -> ExtractionSettings:
    return ExtractionSettings(
        extraction_base_url="http://localhost:11434",
        database_url="postgresql://localhost/test",
    )


def _entity_claim(name="Acme Corp", entity_type="Legal_Entity", resolved_gid=None, **kw) -> Claim:
    return Claim(
        entity_type=entity_type,
        subject_name=name,
        subject_type=entity_type,
        predicate="entity",
        properties_json={"name": name},
        confidence=0.85,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        resolved_entity_grace_id=resolved_gid,
        **kw,
    )


def _rel_claim(subj="Acme Corp", pred="owns", obj="Sub Corp", subj_type="Legal_Entity", obj_type="Legal_Entity", **kw) -> Claim:
    return Claim(
        relationship_type=pred,
        subject_name=subj,
        subject_type=subj_type,
        predicate=pred,
        object_name=obj,
        object_type=obj_type,
        properties_json={},
        confidence=0.85,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        source_document_id="doc-1",
        **kw,
    )


def _batch(claims, entities=None, relationships=None):
    return ExtractionBatch(
        document_id="doc-1",
        claims=claims,
        entities=entities or [],
        relationships=relationships or [],
        claims_accepted=len([c for c in claims if c.status == ClaimStatus.AUTO_ACCEPTED]),
        claims_quarantined=0,
    )


SCHEMA = {
    "entity_types": {"Legal_Entity": {"properties": {"name": {"data_type": "string"}}}},
    "relationships": {"owns": {"source_type": "Legal_Entity", "target_type": "Legal_Entity"}},
}


@pytest.mark.asyncio
class TestWriteEntity:
    async def test_write_entity_from_claim(self):
        claim = _entity_claim()
        batch = _batch([claim])
        client = AsyncMock()
        session = MagicMock()

        # get_extraction_event returns verified status
        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            mock_insert.return_value = EntityCreateResponse(
                grace_id="new-gid", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )
            result = await write_batch(batch, SCHEMA, client, session, "evt-1", _mock_config())

        assert result.entities_created == 1
        mock_insert.assert_called_once()

    async def test_write_new_entity_adds_to_map(self):
        entity_claim = _entity_claim()
        rel_claim = _rel_claim(subj="Acme Corp", obj="Sub Corp")
        sub_claim = _entity_claim(name="Sub Corp", resolved_gid=None)
        batch = _batch([entity_claim, sub_claim, rel_claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock), \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=2), \
             patch("src.extraction.graph_writer.update_event_status_after_write"), \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_insert.side_effect = [
                EntityCreateResponse(grace_id="gid-acme", rid="#1:0", entity_type="Legal_Entity", created=True, canonical_match=False),
                EntityCreateResponse(grace_id="gid-sub", rid="#1:1", entity_type="Legal_Entity", created=True, canonical_match=False),
            ]
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.entities_created == 2

    async def test_write_matched_entity_appends_alias(self):
        claim = _entity_claim(name="ACME Corporation", resolved_gid="existing-gid")
        batch = _batch([claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.get_entity", new_callable=AsyncMock, return_value={"name": "Acme Corp"}), \
             patch("src.extraction.graph_writer.append_entity_alias", new_callable=AsyncMock, return_value=True) as mock_alias, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.entities_matched == 1
        assert result.aliases_appended == 1
        mock_alias.assert_called_once()

    async def test_alias_not_appended_when_names_match(self):
        claim = _entity_claim(name="acme corp", resolved_gid="existing-gid")
        batch = _batch([claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.get_entity", new_callable=AsyncMock, return_value={"name": "Acme Corp"}), \
             patch("src.extraction.graph_writer.append_entity_alias", new_callable=AsyncMock) as mock_alias, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.entities_matched == 1
        assert result.aliases_appended == 0
        mock_alias.assert_not_called()


@pytest.mark.asyncio
class TestRelationshipEndpointResolution:
    async def test_endpoint_resolution_from_map(self):
        entity_claim = _entity_claim(name="Acme Corp", resolved_gid=None)
        sub_claim = _entity_claim(name="Sub Corp", resolved_gid=None)
        rel_claim = _rel_claim()
        batch = _batch([entity_claim, sub_claim, rel_claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock) as mock_rel, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=2), \
             patch("src.extraction.graph_writer.update_event_status_after_write"), \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_insert.side_effect = [
                EntityCreateResponse(grace_id="gid-acme", rid="#1:0", entity_type="Legal_Entity", created=True, canonical_match=False),
                EntityCreateResponse(grace_id="gid-sub", rid="#1:1", entity_type="Legal_Entity", created=True, canonical_match=False),
            ]
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.relationships_created == 1
        mock_rel.assert_called_once()

    async def test_endpoint_fallback_to_arcade(self):
        rel_claim = _rel_claim()
        batch = _batch([rel_claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.canonical_lookup", new_callable=AsyncMock) as mock_lookup, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock), \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=0), \
             patch("src.extraction.graph_writer.update_event_status_after_write"), \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_lookup.side_effect = ["gid-acme", "gid-sub"]
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.relationships_created == 1

    async def test_unresolvable_endpoint_quarantines(self):
        rel_claim = _rel_claim()
        batch = _batch([rel_claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.canonical_lookup", new_callable=AsyncMock, return_value=None), \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=0), \
             patch("src.extraction.graph_writer.update_event_status_after_write"), \
             patch("src.extraction.graph_writer.update_claim_violations"):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.relationships_created == 0
        assert rel_claim.status == ClaimStatus.QUARANTINED
        error_rules = [v.rule for v in rel_claim.constraint_violations if v.severity == ConstraintSeverity.ERROR]
        assert "unresolvable_endpoint" in error_rules

    async def test_relationship_sets_resolved_ids(self):
        rel_claim = _rel_claim()
        batch = _batch([rel_claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.canonical_lookup", new_callable=AsyncMock) as mock_lookup, \
             patch("src.extraction.graph_writer.insert_relationship", new_callable=AsyncMock), \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=0), \
             patch("src.extraction.graph_writer.update_event_status_after_write"), \
             patch("src.extraction.graph_writer.update_claim_resolved_endpoints"):
            mock_lookup.side_effect = ["gid-subj", "gid-obj"]
            await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert rel_claim.resolved_subject_grace_id == "gid-subj"
        assert rel_claim.resolved_object_grace_id == "gid-obj"


@pytest.mark.asyncio
class TestPartialFailure:
    async def test_partial_failure_continues(self):
        c1 = _entity_claim(name="Good Corp")
        c2 = _entity_claim(name="Bad Corp")
        batch = _batch([c1, c2])

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("ArcadeDB down")
            return EntityCreateResponse(
                grace_id="gid-good", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock, side_effect=side_effect), \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.entities_created == 1
        assert result.entities_failed == 1
        assert len(result.errors) == 1


@pytest.mark.asyncio
class TestTemporalTags:
    async def test_temporal_tags_applied(self):
        claim = _entity_claim()
        entity = ExtractedEntity(
            name="Acme Corp", entity_type="Legal_Entity",
            temporal_hints={"start": "January 2024", "end": "December 2024"},
        )
        batch = _batch([claim], entities=[entity])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "verified"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            mock_insert.return_value = EntityCreateResponse(
                grace_id="gid-1", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )
            await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        call_args = mock_insert.call_args[0]
        entity_create = call_args[1]
        assert entity_create.valid_from is not None
        assert entity_create.valid_to is not None


@pytest.mark.asyncio
class TestIdempotency:
    async def test_idempotent_skip_graph_written(self):
        batch = _batch([])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "graph_written"}):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.skipped is True
        assert result.reason == "already_written"

    async def test_idempotent_skip_legacy_completed(self):
        batch = _batch([])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "completed"}):
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.skipped is True
        assert result.reason == "already_written"

    async def test_refuses_running_status(self):
        batch = _batch([])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "running"}):
            with pytest.raises(ValueError, match="running"):
                await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

    async def test_retry_after_partial_failed(self):
        claim = _entity_claim()
        batch = _batch([claim])

        with patch("src.extraction.graph_writer.get_extraction_event", return_value={"status": "partial_failed"}), \
             patch("src.extraction.graph_writer.insert_entity", new_callable=AsyncMock) as mock_insert, \
             patch("src.extraction.graph_writer.create_extraction_event_vertex", new_callable=AsyncMock, return_value="evt-gid"), \
             patch("src.extraction.graph_writer.create_produced_by_edges", new_callable=AsyncMock, return_value=1), \
             patch("src.extraction.graph_writer.update_event_status_after_write"):
            mock_insert.return_value = EntityCreateResponse(
                grace_id="gid-1", rid="#1:0", entity_type="Legal_Entity",
                created=True, canonical_match=False,
            )
            result = await write_batch(batch, SCHEMA, AsyncMock(), MagicMock(), "evt-1", _mock_config())

        assert result.entities_created == 1
        assert not result.skipped
