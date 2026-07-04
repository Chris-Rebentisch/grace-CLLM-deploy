"""D452 — Verify accept-path stamps decay-eligibility properties on graph inserts.

Tests that ``promote_claim_to_graph`` stamps ``last_verified_at``,
``confidence_at_verification``, and ``verdict='SUPPORTED'`` on both
entity and relationship property dicts before insert.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.claim_models import Claim, ClaimStatus


def _make_entity_claim(**overrides) -> Claim:
    defaults = {
        "claim_id": "test-entity-claim-001",
        "extraction_event_id": "evt-001",
        "schema_version": 1,
        "ontology_module": "test_module",
        "source_document_id": "doc-001",
        "entity_type": "Legal_Entity",
        "subject_name": "TestCorp",
        "relationship_type": None,
        "object_type": None,
        "object_name": None,
        "subject_type": None,
        "properties_json": {},
        "resolved_subject_grace_id": None,
        "resolved_object_grace_id": None,
        "status": ClaimStatus.QUARANTINED,
        "decision_source": "pipeline",
        "supersedes_claim_id": None,
    }
    defaults.update(overrides)
    return Claim(**defaults)


def _make_relationship_claim(**overrides) -> Claim:
    defaults = {
        "claim_id": "test-rel-claim-001",
        "extraction_event_id": "evt-001",
        "schema_version": 1,
        "ontology_module": "test_module",
        "source_document_id": "doc-001",
        "entity_type": None,
        "subject_name": "TestCorp",
        "relationship_type": "Has_Subsidiary",
        "object_type": "Legal_Entity",
        "object_name": "SubCorp",
        "subject_type": "Legal_Entity",
        "properties_json": {},
        "resolved_subject_grace_id": "grace-subj-001",
        "resolved_object_grace_id": "grace-obj-001",
        "status": ClaimStatus.QUARANTINED,
        "decision_source": "pipeline",
        "supersedes_claim_id": None,
    }
    defaults.update(overrides)
    return Claim(**defaults)


class TestEntityStamp:
    """Accepted entity claim carries decay-eligibility properties."""

    def test_entity_stamp_present(self) -> None:
        """Entity insert receives last_verified_at, confidence_at_verification, verdict."""
        claim = _make_entity_claim()
        mock_session = MagicMock()
        mock_arcade = AsyncMock()

        captured_entity = {}

        async def fake_insert(client, entity):
            captured_entity["properties"] = entity.properties
            result = MagicMock()
            result.created = True
            result.grace_id = "grace-001"
            return result

        with (
            patch("src.extraction.claim_override_writer.insert_entity", side_effect=fake_insert),
            patch("src.extraction.claim_override_writer.update_claim_resolved_endpoints"),
            patch("src.extraction.claim_override_writer.update_claim_status"),
            patch("src.extraction.claim_override_writer._stamp_human_decided_at"),
        ):
            asyncio.run(
                __import__(
                    "src.extraction.claim_override_writer", fromlist=["promote_claim_to_graph"]
                ).promote_claim_to_graph(
                    claim=claim,
                    reviewer="test-reviewer",
                    notes=None,
                    session=mock_session,
                    arcade_client=mock_arcade,
                )
            )

        props = captured_entity["properties"]
        assert "last_verified_at" in props
        assert "confidence_at_verification" in props
        assert props["verdict"] == "SUPPORTED"
        # Verify ISO 8601 format
        datetime.fromisoformat(props["last_verified_at"])


class TestRelationshipStamp:
    """Accepted relationship claim carries decay-eligibility properties."""

    def test_relationship_stamp_present(self) -> None:
        """Relationship insert receives last_verified_at, confidence_at_verification, verdict."""
        claim = _make_relationship_claim()
        mock_session = MagicMock()
        mock_arcade = AsyncMock()

        captured_rel = {}

        async def fake_insert_rel(client, rel):
            captured_rel["properties"] = rel.properties
            return None

        with (
            patch("src.extraction.claim_override_writer.insert_relationship", side_effect=fake_insert_rel),
            patch("src.extraction.claim_override_writer.update_claim_status"),
            patch("src.extraction.claim_override_writer._stamp_human_decided_at"),
        ):
            asyncio.run(
                __import__(
                    "src.extraction.claim_override_writer", fromlist=["promote_claim_to_graph"]
                ).promote_claim_to_graph(
                    claim=claim,
                    reviewer="test-reviewer",
                    notes=None,
                    session=mock_session,
                    arcade_client=mock_arcade,
                )
            )

        props = captured_rel["properties"]
        assert "last_verified_at" in props
        assert "confidence_at_verification" in props
        assert props["verdict"] == "SUPPORTED"
        datetime.fromisoformat(props["last_verified_at"])


class TestConfigDrivenValue:
    """Confidence value is driven by config/decay_config.yaml default."""

    def test_default_confidence_value(self) -> None:
        """Default confidence_at_verification matches config (0.9)."""
        claim = _make_entity_claim()
        mock_session = MagicMock()
        mock_arcade = AsyncMock()

        captured_entity = {}

        async def fake_insert(client, entity):
            captured_entity["properties"] = entity.properties
            result = MagicMock()
            result.created = True
            result.grace_id = "grace-001"
            return result

        with (
            patch("src.extraction.claim_override_writer.insert_entity", side_effect=fake_insert),
            patch("src.extraction.claim_override_writer.update_claim_resolved_endpoints"),
            patch("src.extraction.claim_override_writer.update_claim_status"),
            patch("src.extraction.claim_override_writer._stamp_human_decided_at"),
        ):
            asyncio.run(
                __import__(
                    "src.extraction.claim_override_writer", fromlist=["promote_claim_to_graph"]
                ).promote_claim_to_graph(
                    claim=claim,
                    reviewer="test-reviewer",
                    notes=None,
                    session=mock_session,
                    arcade_client=mock_arcade,
                )
            )

        assert captured_entity["properties"]["confidence_at_verification"] == 0.9
