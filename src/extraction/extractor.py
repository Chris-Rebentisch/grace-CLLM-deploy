"""Core async extraction function for a single document chunk.

Wraps ExtractionLLMClient with prompt construction, overlap filtering,
latency tracking, and error handling.
"""

from __future__ import annotations

import time

import structlog
from pydantic import BaseModel

from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    DocumentChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
)
from src.extraction.extraction_prompts import build_system_prompt, build_user_prompt
from src.extraction.instructor_client import ExtractionLLMClient, ExtractionLLMError

log = structlog.get_logger()


class ExtractedChunkResult(BaseModel):
    """Result of extracting a single chunk. Wraps success/failure."""

    chunk_id: str
    success: bool
    result: ExtractionResult | None = None
    error: str | None = None
    latency_ms: float = 0.0


async def extract_chunk(
    chunk: DocumentChunk,
    schema: dict,
    client: ExtractionLLMClient,
    config: ExtractionSettings,
) -> ExtractedChunkResult:
    """Extract entities and relationships from a single chunk.

    Steps:
    1. Build system prompt with ontology schema
    2. Build user prompt with sentence-annotated chunk text
    3. Call client.extract() with ExtractionResult response_model
    4. If successful: filter overlap-only entities/relationships (D63)
    5. If ExtractionLLMError: return failure result (D68)
    6. Track latency via time.perf_counter() delta
    """
    system_prompt = build_system_prompt(schema)
    user_prompt = build_user_prompt(
        chunk.text, chunk.sentence_offsets, chunk.overlap_char_count
    )

    start = time.perf_counter()
    try:
        result = await client.extract(system_prompt, user_prompt, ExtractionResult)
        latency_ms = (time.perf_counter() - start) * 1000

        # Filter overlap-only entities/relationships (D63)
        result = _filter_overlap_entities(
            result, chunk.sentence_offsets, chunk.overlap_char_count
        )

        return ExtractedChunkResult(
            chunk_id=chunk.chunk_id,
            success=True,
            result=result,
            latency_ms=latency_ms,
        )
    except ExtractionLLMError as e:
        latency_ms = (time.perf_counter() - start) * 1000
        log.warning(
            "chunk_extraction_failed",
            chunk_id=chunk.chunk_id,
            error=str(e),
        )
        return ExtractedChunkResult(
            chunk_id=chunk.chunk_id,
            success=False,
            error=str(e),
            latency_ms=latency_ms,
        )


def _is_overlap_only(
    indices: list[int],
    sentence_offsets: list[tuple[int, int]],
    overlap_char_count: int,
) -> bool:
    """Determine if all cited sentence indices are wholly within overlap.

    Returns True (exclude) only when every valid index is wholly in overlap
    and there are no invalid indices. Conservative: invalid or empty indices
    mean keep the item.
    """
    if not indices:
        return False

    has_valid = False
    for idx in indices:
        if idx < 0 or idx >= len(sentence_offsets):
            # Invalid index counts as outside overlap -> keep
            return False
        has_valid = True
        _start, end = sentence_offsets[idx]
        if end > overlap_char_count:
            # At least one valid sentence extends past overlap -> keep
            return False

    # All valid indices are wholly in overlap (and we had at least one valid)
    return has_valid


def _filter_overlap_entities(
    result: ExtractionResult,
    sentence_offsets: list[tuple[int, int]],
    overlap_char_count: int,
) -> ExtractionResult:
    """Remove entities/relationships whose ALL cited sentences are wholly
    within the overlap region.

    If overlap_char_count == 0: return result unchanged.
    If sentence_offsets is empty: return result unchanged (skip filtering).
    """
    if overlap_char_count == 0:
        return result
    if not sentence_offsets:
        return result

    filtered_entities = [
        e for e in result.entities
        if not _is_overlap_only(
            e.source_sentence_indices, sentence_offsets, overlap_char_count
        )
    ]

    filtered_relationships = [
        r for r in result.relationships
        if not _is_overlap_only(
            r.source_sentence_indices, sentence_offsets, overlap_char_count
        )
    ]

    return ExtractionResult(
        entities=filtered_entities,
        relationships=filtered_relationships,
    )
