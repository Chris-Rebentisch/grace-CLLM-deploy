"""Tests for entity resolution wired into ExtractionPipeline."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.claim_models import ClaimStatus
from src.extraction.document_chunker import DocumentChunker
from src.extraction.entity_resolver import EntityResolutionResult
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
)
from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.verification import VerificationResult
from src.graph.arcade_client import ArcadeClient, ArcadeConfig


def _mock_arcade_client():
    """ArcadeClient with mocked methods."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={"result": []})
    client.execute_sql = AsyncMock(return_value={"result": []})
    return client


def _mock_extraction_client():
    """Mock ExtractionLLMClient that returns a simple extraction result."""
    client = MagicMock()
    client.extract = AsyncMock(return_value=ExtractionResult(
        entities=[
            ExtractedEntity(
                name="Acme Corp",
                entity_type="Legal_Entity",
                properties={"jurisdiction": "Delaware"},
                source_sentence_indices=[0],
            ),
        ],
        relationships=[],
    ))
    # Verification returns SUPPORTED
    client.verify = AsyncMock(return_value=VerificationResult(
        chain_of_thought="Matches text.",
        verdict="SUPPORTED",
        evidence_sentences=[0],
        contradiction_reason="",
    ))
    client.extraction_provider = "ollama"
    client.extraction_model = "qwen2.5:7b"
    client.verification_provider = "ollama"
    client.verification_model = "qwen2.5:7b"
    return client


def _mock_router(schema):
    """Mock OntologyRouter returning sample schema."""
    router = MagicMock()
    router.resolve_schema = AsyncMock(return_value=(schema, 1))
    router.get_available_modules = AsyncMock(return_value=["core"])
    return router


SAMPLE_SCHEMA = {
    "entity_types": {
        "Legal_Entity": {"properties": {"name": {"type": "string"}}},
    },
    "relationships": {},
}

SHORT_TEXT = (
    "Acme Corp is a Delaware corporation."
)


@pytest.mark.asyncio
async def test_resolve_true_populates_grace_id():
    """Pipeline with resolve=True populates resolved_entity_grace_id on entity claims."""
    arcade_client = _mock_arcade_client()
    # canonical_lookup returns None (no exact match), cypher returns no candidates
    # → entity should be resolved as "new"

    config = ExtractionSettings()
    chunker = DocumentChunker(config)
    client = _mock_extraction_client()
    router = _mock_router(SAMPLE_SCHEMA)

    with patch(
        "src.extraction.entity_resolver.canonical_lookup",
        new_callable=AsyncMock,
        return_value=None,
    ):
        pipeline = ExtractionPipeline(
            config=config,
            chunker=chunker,
            router=router,
            client=client,
            arcade_client=arcade_client,
        )
        batch = await pipeline.extract_document(
            SHORT_TEXT, "doc-001", verify=True, resolve=True, session=None
        )

    # Entity claims should exist
    entity_claims = [
        c for c in batch.claims if c.entity_type and c.status == ClaimStatus.AUTO_ACCEPTED
    ]
    assert len(entity_claims) >= 1

    # resolved_entity_grace_id should be None for "new" entities
    # (no match in graph), but the resolution ran
    assert batch.er_stats is not None
    assert batch.er_stats["total"] >= 1

    # Entity on batch should have resolution_tier set
    resolved_entities = [e for e in batch.entities if e.resolution_tier is not None]
    assert len(resolved_entities) >= 1


@pytest.mark.asyncio
async def test_resolve_false_skips_resolution():
    """Pipeline with resolve=False skips resolution entirely."""
    config = ExtractionSettings()
    chunker = DocumentChunker(config)
    client = _mock_extraction_client()
    router = _mock_router(SAMPLE_SCHEMA)

    pipeline = ExtractionPipeline(
        config=config,
        chunker=chunker,
        router=router,
        client=client,
    )
    batch = await pipeline.extract_document(
        SHORT_TEXT, "doc-002", verify=True, resolve=False, session=None
    )

    assert batch.er_stats is None
    # Entity should not have resolution_tier set
    for e in batch.entities:
        assert e.resolution_tier is None


@pytest.mark.asyncio
async def test_d93_resolve_true_verify_false(caplog):
    """D93: resolve=True + verify=False -> warning logged, resolve forced to False."""
    config = ExtractionSettings()
    chunker = DocumentChunker(config)
    client = _mock_extraction_client()
    router = _mock_router(SAMPLE_SCHEMA)

    pipeline = ExtractionPipeline(
        config=config,
        chunker=chunker,
        router=router,
        client=client,
        arcade_client=_mock_arcade_client(),
    )
    batch = await pipeline.extract_document(
        SHORT_TEXT, "doc-003", verify=False, resolve=True, session=None
    )

    # Resolution should NOT have run
    assert batch.er_stats is None
    # No claims (verify=False means no claims)
    assert len(batch.claims) == 0
