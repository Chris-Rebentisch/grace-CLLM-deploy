"""Tests for extract_chunk and overlap filtering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    DocumentChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
)
from src.extraction.extractor import (
    ExtractedChunkResult,
    _filter_overlap_entities,
    extract_chunk,
)
from src.extraction.instructor_client import ExtractionLLMError


def _make_chunk(**overrides) -> DocumentChunk:
    defaults = {
        "chunk_id": "test-chunk-001",
        "text": "Acme Corp signed a deal. The contract was effective January 2024.",
        "char_start": 0,
        "char_end": 64,
        "sentence_offsets": [(0, 24), (25, 64)],
    }
    defaults.update(overrides)
    return DocumentChunk(**defaults)


def _make_client(return_value=None, side_effect=None):
    client = MagicMock()
    client.extract = AsyncMock(return_value=return_value, side_effect=side_effect)
    return client


class TestExtractChunk:
    @pytest.mark.asyncio
    async def test_success_returns_result(self, sample_extraction_result):
        """Successful extraction returns ExtractedChunkResult with success=True."""
        client = _make_client(return_value=sample_extraction_result)
        chunk = _make_chunk()
        config = ExtractionSettings()
        schema = {"entity_types": {"Legal_Entity": {}}, "relationships": {}}

        result = await extract_chunk(chunk, schema, client, config)

        assert result.success is True
        assert result.result is not None
        assert result.chunk_id == "test-chunk-001"

    @pytest.mark.asyncio
    async def test_failure_returns_error(self):
        """ExtractionLLMError returns success=False with error message."""
        client = _make_client(
            side_effect=ExtractionLLMError("timeout", provider="ollama", model="qwen2.5:7b")
        )
        chunk = _make_chunk()
        config = ExtractionSettings()
        schema = {"entity_types": {}, "relationships": {}}

        result = await extract_chunk(chunk, schema, client, config)

        assert result.success is False
        assert result.error is not None
        assert "timeout" in result.error

    @pytest.mark.asyncio
    async def test_latency_recorded(self, sample_extraction_result):
        """latency_ms is > 0 on success."""
        client = _make_client(return_value=sample_extraction_result)
        chunk = _make_chunk()
        config = ExtractionSettings()
        schema = {"entity_types": {}, "relationships": {}}

        result = await extract_chunk(chunk, schema, client, config)

        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_empty_result_is_success(self):
        """ExtractionResult with zero entities is success, not failure."""
        empty_result = ExtractionResult(entities=[], relationships=[])
        client = _make_client(return_value=empty_result)
        chunk = _make_chunk()
        config = ExtractionSettings()
        schema = {"entity_types": {}, "relationships": {}}

        result = await extract_chunk(chunk, schema, client, config)

        assert result.success is True
        assert len(result.result.entities) == 0


class TestOverlapFiltering:
    def test_overlap_only_entities_excluded(self):
        """Entities with all sentence indices wholly in overlap are removed."""
        offsets = [(0, 20), (21, 50), (51, 80)]
        overlap = 25  # sentence 0 end=20 <= 25 -> wholly in overlap

        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[0]),
                ExtractedEntity(name="B", entity_type="T", source_sentence_indices=[0, 1]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, offsets, overlap)
        assert len(filtered.entities) == 1
        assert filtered.entities[0].name == "B"

    def test_straddling_sentence_kept(self):
        """Sentence starting in overlap but ending past it is not overlap-only."""
        offsets = [(0, 30)]  # end=30 > overlap=20
        overlap = 20

        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[0]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, offsets, overlap)
        assert len(filtered.entities) == 1

    def test_no_overlap_no_filtering(self):
        """overlap_char_count=0 returns result unchanged."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[0]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, [(0, 10)], 0)
        assert len(filtered.entities) == 1

    def test_empty_offsets_skip_filtering(self):
        """Empty sentence_offsets skips filtering, keeps all entities."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[0]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, [], 50)
        assert len(filtered.entities) == 1

    def test_no_sentence_indices_kept(self):
        """Entity with empty source_sentence_indices is kept."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, [(0, 10)], 15)
        assert len(filtered.entities) == 1

    def test_invalid_sentence_index_keeps_entity(self):
        """Out-of-range index: item kept (cannot prove overlap-only)."""
        offsets = [(0, 10)]  # only index 0 valid
        overlap = 15

        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="A", entity_type="T", source_sentence_indices=[0, 99]),
            ],
            relationships=[],
        )
        filtered = _filter_overlap_entities(result, offsets, overlap)
        # index 99 is invalid -> treated as outside overlap -> keep
        assert len(filtered.entities) == 1

    def test_relationships_filtered_same_logic(self):
        """Relationships follow same overlap filtering as entities."""
        offsets = [(0, 20), (21, 50)]
        overlap = 25

        result = ExtractionResult(
            entities=[],
            relationships=[
                ExtractedRelationship(
                    subject_name="A", subject_type="T",
                    predicate="rel", object_name="B", object_type="T",
                    source_sentence_indices=[0],  # wholly in overlap
                ),
                ExtractedRelationship(
                    subject_name="C", subject_type="T",
                    predicate="rel", object_name="D", object_type="T",
                    source_sentence_indices=[1],  # not in overlap
                ),
            ],
        )
        filtered = _filter_overlap_entities(result, offsets, overlap)
        assert len(filtered.relationships) == 1
        assert filtered.relationships[0].subject_name == "C"
