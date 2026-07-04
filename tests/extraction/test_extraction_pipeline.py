"""Tests for ExtractionPipeline orchestrator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.extraction.claim_models import ClaimVerdict
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
)
from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.instructor_client import ExtractionLLMError


def _make_pipeline(
    client_return=None,
    client_side_effect=None,
    router_schema=None,
    router_modules=None,
    **kwargs,
):
    """Helper to build pipeline with mocked dependencies."""
    from src.extraction.document_chunker import DocumentChunker

    config = ExtractionSettings()
    chunker = DocumentChunker(config)

    client = MagicMock()
    client.extract = AsyncMock(
        return_value=client_return, side_effect=client_side_effect
    )
    client._extraction_provider = "ollama"
    client._extraction_model = "qwen2.5:7b"
    client.extraction_provider = "ollama"
    client.extraction_model = "qwen2.5:7b"
    client.verification_provider = "ollama"
    client.verification_model = "qwen2.5:7b"

    # Default verification mock (SUPPORTED)
    from src.extraction.verification import VerificationResult
    mock_vr = VerificationResult(
        chain_of_thought="ok", verdict="SUPPORTED",
        evidence_sentences=[0], contradiction_reason="",
    )
    client.verify = AsyncMock(return_value=mock_vr)

    router = MagicMock()
    if router_schema is None:
        router_schema = {
            "entity_types": {"Legal_Entity": {}, "Contract": {}},
            "relationships": {"party_to": {"domain": "Legal_Entity", "range": "Contract"}},
        }
    router.resolve_schema = AsyncMock(return_value=(router_schema, None))
    router.get_available_modules = AsyncMock(return_value=router_modules or ["core"])

    return ExtractionPipeline(
        config=config, chunker=chunker, router=router, client=client, **kwargs
    )


class TestPipelineEndToEnd:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, sample_extraction_result):
        """chunk -> route -> extract -> dedup -> ExtractionBatch."""
        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Acme Corp signed a service agreement. The deal was in Delaware."

        batch = await pipeline.extract_document(text, "doc-001", verify=False)

        assert batch.document_id == "doc-001"
        assert batch.chunks_total >= 1
        assert batch.chunks_succeeded >= 1
        assert batch.chunks_failed == 0
        assert len(batch.chunk_entity_counts) == batch.chunks_total
        assert len(batch.chunk_extraction_succeeded) == batch.chunks_total
        assert len(batch.chunk_latency_ms) == batch.chunks_total

    @pytest.mark.asyncio
    async def test_batch_metadata(self, sample_extraction_result):
        """ExtractionBatch has provider, model, timestamps, counts."""
        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Some text for extraction."

        batch = await pipeline.extract_document(text, "doc-002", verify=False)

        assert batch.provider_used == "ollama"
        assert batch.model_used == "qwen2.5:7b"
        assert batch.started_at is not None
        assert batch.completed_at is not None

    @pytest.mark.asyncio
    async def test_empty_document(self, sample_extraction_result):
        """Empty text returns ExtractionBatch with zero chunks."""
        pipeline = _make_pipeline(client_return=sample_extraction_result)

        batch = await pipeline.extract_document("", "doc-003", verify=False)

        assert batch.chunks_total == 0
        assert batch.chunks_succeeded == 0
        assert len(batch.entities) == 0


class TestEntityDedup:
    @pytest.mark.asyncio
    async def test_same_name_same_type_merge(self):
        """Same entity from two chunks merges into one via direct dedup call."""
        pipeline = _make_pipeline()
        entities = [
            ExtractedEntity(
                name="Acme Holdings", entity_type="Legal_Entity",
                properties={"jurisdiction": "Delaware"},
                source_sentence_indices=[0],
            ),
            ExtractedEntity(
                name="Acme Holdings", entity_type="Legal_Entity",
                properties={"jurisdiction": "New York"},
                source_sentence_indices=[5],
            ),
        ]
        deduped, counts = pipeline._dedup_entities(entities)
        assert len(deduped) == 1
        assert deduped[0].name == "Acme Holdings"
        # Later properties win
        assert deduped[0].properties["jurisdiction"] == "New York"

    @pytest.mark.asyncio
    async def test_normalization_case_insensitive(self):
        """'Acme Corp' and 'acme corp' merge."""
        pipeline = _make_pipeline()
        entities = [
            ExtractedEntity(name="Acme Corp", entity_type="Legal_Entity"),
            ExtractedEntity(name="acme corp", entity_type="Legal_Entity"),
        ]
        deduped, _ = pipeline._dedup_entities(entities)
        assert len(deduped) == 1
        assert deduped[0].name == "Acme Corp"  # keeps first casing

    @pytest.mark.asyncio
    async def test_normalization_suffix_strip(self):
        """'Acme Holdings, LLC' and 'Acme Holdings' merge."""
        pipeline = _make_pipeline()
        entities = [
            ExtractedEntity(name="Acme Holdings, LLC", entity_type="Legal_Entity"),
            ExtractedEntity(name="Acme Holdings", entity_type="Legal_Entity"),
        ]
        deduped, _ = pipeline._dedup_entities(entities)
        assert len(deduped) == 1

    @pytest.mark.asyncio
    async def test_different_types_no_merge(self):
        """Same name but different entity_type -> no merge."""
        pipeline = _make_pipeline()
        entities = [
            ExtractedEntity(name="Agreement", entity_type="Contract"),
            ExtractedEntity(name="Agreement", entity_type="Legal_Entity"),
        ]
        deduped, _ = pipeline._dedup_entities(entities)
        assert len(deduped) == 2

    @pytest.mark.asyncio
    async def test_sentence_indices_union(self):
        """Merged entity has union of source_sentence_indices."""
        pipeline = _make_pipeline()
        entities = [
            ExtractedEntity(
                name="Acme", entity_type="Legal_Entity",
                source_sentence_indices=[0, 2],
            ),
            ExtractedEntity(
                name="Acme", entity_type="Legal_Entity",
                source_sentence_indices=[1, 3],
            ),
        ]
        deduped, _ = pipeline._dedup_entities(entities)
        assert len(deduped) == 1
        assert deduped[0].source_sentence_indices == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_pre_dedup_count(self, sample_extraction_result):
        """entities_pre_dedup_count reflects count before merge."""
        # Each chunk returns 2 entities; if 2 chunks, pre_dedup = 4
        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Acme Corp signed a deal.\n\n" * 200

        batch = await pipeline.extract_document(text, "doc-predeup", verify=False)

        assert batch.entities_pre_dedup_count >= batch.chunks_succeeded * 2
        assert batch.entities_pre_dedup_count >= len(batch.entities)


class TestRelationshipDedup:
    @pytest.mark.asyncio
    async def test_same_triple_merge(self):
        """Same subject-predicate-object from different chunks merge."""
        pipeline = _make_pipeline()
        rels = [
            ExtractedRelationship(
                subject_name="Acme", subject_type="Legal_Entity",
                predicate="party_to", object_name="Deal", object_type="Contract",
                source_sentence_indices=[0],
            ),
            ExtractedRelationship(
                subject_name="Acme", subject_type="Legal_Entity",
                predicate="party_to", object_name="Deal", object_type="Contract",
                source_sentence_indices=[5],
            ),
        ]
        deduped, _ = pipeline._dedup_relationships(rels)
        assert len(deduped) == 1
        assert deduped[0].source_sentence_indices == [0, 5]

    @pytest.mark.asyncio
    async def test_different_predicate_no_merge(self):
        """Same entities but different predicate -> no merge."""
        pipeline = _make_pipeline()
        rels = [
            ExtractedRelationship(
                subject_name="A", subject_type="T",
                predicate="party_to", object_name="B", object_type="T",
            ),
            ExtractedRelationship(
                subject_name="A", subject_type="T",
                predicate="signed_by", object_name="B", object_type="T",
            ),
        ]
        deduped, _ = pipeline._dedup_relationships(rels)
        assert len(deduped) == 2

    @pytest.mark.asyncio
    async def test_predicate_casefold_merge(self):
        """party_to and Party_To merge after predicate normalization (D62)."""
        pipeline = _make_pipeline()
        rels = [
            ExtractedRelationship(
                subject_name="A", subject_type="T",
                predicate="party_to", object_name="B", object_type="T",
            ),
            ExtractedRelationship(
                subject_name="A", subject_type="T",
                predicate="Party_To", object_name="B", object_type="T",
            ),
        ]
        deduped, _ = pipeline._dedup_relationships(rels)
        assert len(deduped) == 1
        assert deduped[0].predicate == "party_to"  # keeps first casing


class TestModuleGuard:
    @pytest.mark.asyncio
    async def test_single_module_no_name_ok(self):
        """One module available + no module_name -> uses full schema."""
        pipeline = _make_pipeline(
            client_return=ExtractionResult(relationships=[]),
            router_modules=["core"],
        )
        text = "Some text."

        batch = await pipeline.extract_document(text, "doc-single", verify=False)
        assert batch.chunks_total >= 1

    @pytest.mark.asyncio
    async def test_multi_module_no_name_error(self):
        """Two modules + no module_name -> ValueError."""
        pipeline = _make_pipeline(
            client_return=ExtractionResult(relationships=[]),
            router_modules=["core", "finance"],
        )

        with pytest.raises(ValueError, match="Multiple ontology modules"):
            await pipeline.extract_document("Some text.", "doc-multi", verify=False)

    @pytest.mark.asyncio
    async def test_schema_not_found_error(self):
        """resolve_schema returns None -> ValueError."""
        pipeline = _make_pipeline(
            client_return=ExtractionResult(relationships=[]),
            router_schema=None,
            router_modules=[],
        )
        # Override resolve_schema to return None
        pipeline._router.resolve_schema = AsyncMock(return_value=(None, None))

        with pytest.raises(ValueError, match="Schema not found"):
            await pipeline.extract_document("Some text.", "doc-nf", verify=False)


class TestPartialFailure:
    @pytest.mark.asyncio
    async def test_some_chunks_fail(self):
        """Pipeline continues when some chunks fail."""
        call_count = 0

        async def alternating_extract(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ExtractionLLMError("fail")
            return ExtractionResult(
                entities=[ExtractedEntity(name="E", entity_type="T")],
                relationships=[],
            )

        # Use small chunk cap to force multiple chunks
        config = ExtractionSettings(chunk_token_cap=200, chunk_overlap_tokens=0)
        from src.extraction.document_chunker import DocumentChunker

        chunker = DocumentChunker(config)
        client = MagicMock()
        client.extract = AsyncMock(side_effect=alternating_extract)
        client._extraction_provider = "ollama"
        client._extraction_model = "qwen2.5:7b"
        client.extraction_provider = "ollama"
        client.extraction_model = "qwen2.5:7b"
        router = MagicMock()
        router.resolve_schema = AsyncMock(return_value=({"entity_types": {}, "relationships": {}}, None))
        router.get_available_modules = AsyncMock(return_value=["core"])

        pipeline = ExtractionPipeline(
            config=config, chunker=chunker, router=router, client=client
        )
        text = "Paragraph about business topics and entities.\n\n" * 50

        batch = await pipeline.extract_document(text, "doc-partial", verify=False)

        assert batch.chunks_failed > 0
        assert batch.chunks_succeeded > 0
        assert batch.chunks_total == batch.chunks_succeeded + batch.chunks_failed

    @pytest.mark.asyncio
    async def test_all_chunks_fail(self):
        """All-fail returns ExtractionBatch with chunks_failed == total."""
        pipeline = _make_pipeline(
            client_side_effect=ExtractionLLMError("all fail")
        )
        text = "Some text for extraction."

        batch = await pipeline.extract_document(text, "doc-allfail", verify=False)

        assert batch.chunks_failed == batch.chunks_total
        assert batch.chunks_succeeded == 0
        assert len(batch.entities) == 0


class TestChunkSourceMap:
    @pytest.mark.asyncio
    async def test_dedup_preserves_chunk_provenance(
        self, multi_chunk_entities_for_dedup
    ):
        """Merged entity has chunk_source_map from two different chunk_ids."""
        pipeline = _make_pipeline()
        deduped, counts = pipeline._dedup_entities(multi_chunk_entities_for_dedup)
        assert len(deduped) == 1
        merged = deduped[0]
        assert len(merged.chunk_source_map) == 2
        chunk_ids = {cid for cid, _ in merged.chunk_source_map}
        assert chunk_ids == {"chunk_0", "chunk_1"}

    @pytest.mark.asyncio
    async def test_collision_resolved(self, multi_chunk_entities_for_dedup):
        """Same sentence index from different chunks: distinct (chunk_id, index) pairs."""
        pipeline = _make_pipeline()
        deduped, _ = pipeline._dedup_entities(multi_chunk_entities_for_dedup)
        merged = deduped[0]
        # Both have sentence index 2, but different chunk_ids
        assert ("chunk_0", 2) in merged.chunk_source_map
        assert ("chunk_1", 2) in merged.chunk_source_map


class TestPipelineVerification:
    @pytest.mark.asyncio
    async def test_pipeline_verify_true(self, sample_extraction_result):
        """verify=True produces claims with verdicts and confidence scores."""
        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Acme Corp signed a service agreement. The deal was in Delaware."

        batch = await pipeline.extract_document(text, "doc-v", verify=True)

        assert len(batch.claims) > 0
        for claim in batch.claims:
            assert claim.verdict in (
                ClaimVerdict.SUPPORTED, ClaimVerdict.REFUTED, ClaimVerdict.INSUFFICIENT
            )
            assert claim.confidence is not None
            assert claim.confidence > 0
        assert batch.claims_accepted >= 0
        assert batch.avg_claim_confidence is not None

    @pytest.mark.asyncio
    async def test_pipeline_verify_false(self, sample_extraction_result):
        """verify=False skips verification, no claims created."""
        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Acme Corp signed a service agreement."

        batch = await pipeline.extract_document(text, "doc-nv", verify=False)

        assert batch.claims == []
        assert batch.claims_accepted == 0
        assert batch.claims_quarantined == 0
        assert batch.avg_claim_confidence is None
        assert batch.verification_failure_count == 0

    @pytest.mark.asyncio
    async def test_pipeline_creates_extraction_event(
        self, sample_extraction_result, db_session
    ):
        """When session provided, extraction event is inserted."""
        from src.extraction.claim_database import get_extraction_event

        pipeline = _make_pipeline(client_return=sample_extraction_result)
        text = "Acme Corp is a Delaware corporation."

        batch = await pipeline.extract_document(
            text, "doc-evt", verify=True, session=db_session
        )

        assert len(batch.claims) > 0
        event_id = batch.claims[0].extraction_event_id
        assert event_id is not None
        event = get_extraction_event(db_session, event_id)
        assert event is not None
        assert event["status"] == "verified"
