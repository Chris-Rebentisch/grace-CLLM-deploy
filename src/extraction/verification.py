"""Verification module: verify extracted triples against source text.

VerificationResult is the Instructor response_model for verify() calls.
verify_batch orchestrates parallel verification of all entities and
relationships in an ExtractionBatch.
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel, Field

from src.analytics import metrics as grace_metrics
from src.extraction.claim_models import ClaimVerdict, EvidenceSpan
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
)
from src.extraction.instructor_client import ExtractionLLMClient, ExtractionLLMError
from src.extraction.verification_prompts import (
    build_verification_system_prompt,
    build_verification_user_prompt,
    entity_to_hypothesis,
    relationship_to_hypothesis,
)

log = structlog.get_logger()


class VerificationResult(BaseModel):
    """Structured output from the verification model.

    Flat model for 7B reliability. chain_of_thought improves reasoning
    quality but is not persisted to the Claim — consumed at inference
    time only.
    """

    chain_of_thought: str = Field(
        description="Think step by step: what does the source text say? "
                    "Does it support, contradict, or not address the "
                    "claimed fact?"
    )
    verdict: str = Field(
        description="One of: SUPPORTED, REFUTED, INSUFFICIENT"
    )
    evidence_sentences: list[int] = Field(
        default_factory=list,
        description="List of sentence indices [S#] from the source text "
                    "that are relevant to this verdict",
    )
    contradiction_reason: str = Field(
        default="",
        description="If REFUTED, explain what in the source text "
                    "contradicts the claim. Empty string if not REFUTED.",
    )


def record_triple_confidence(
    confidence: float, verdict: ClaimVerdict, ontology_module: str | None
) -> None:
    """Emit one ``grace_extraction_triple_confidence`` observation (spec §5.2).

    Per-triple, at verification time. ``ontology_module=None`` maps to
    the label value ``"unknown"`` (never ``"_init"`` — reserved per D151).
    """
    module_label = ontology_module if ontology_module else "unknown"
    grace_metrics.extraction_triple_confidence.record(
        confidence,
        attributes={
            "ontology_module": module_label,
            "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
        },
    )


def _parse_verdict(verdict_str: str) -> ClaimVerdict:
    """Normalize verification model's verdict string to ClaimVerdict enum.

    Uses exact allowlist matching (D82). Unknown values fall back to
    INSUFFICIENT with a warning log. Negation patterns are not mapped
    to REFUTED — "not supported by text" is semantically INSUFFICIENT,
    not REFUTED; only explicit contradiction is REFUTED.

    F-0025a / ISS-0056: this parser was already correct — the observed
    failure was the judge misapplying the rubric upstream, not the
    mapping here. The hardened system prompt
    (verification_prompts.build_verification_system_prompt) now tells
    the judge explicitly that absence-of-mention is INSUFFICIENT (other
    documents may support the fact; verification is single-document per
    GrACE-Product §10) and that a trade-name/dba is not a contradiction.
    Parsing semantics are intentionally unchanged and regression-pinned
    in tests/extraction/test_verification.py::TestParseVerdict.
    """
    cleaned = verdict_str.strip().rstrip(".").upper()
    if cleaned in ("SUPPORTED", "SUPPORT"):
        return ClaimVerdict.SUPPORTED
    if cleaned in ("REFUTED", "REFUTE", "CONTRADICTED"):
        return ClaimVerdict.REFUTED
    if cleaned in ("INSUFFICIENT", "NEUTRAL", "UNKNOWN"):
        return ClaimVerdict.INSUFFICIENT
    log.warning("verification_unknown_verdict", raw=verdict_str)
    return ClaimVerdict.INSUFFICIENT


def _build_evidence_spans(
    evidence_indices: list[int],
    sentence_offsets: list[tuple[int, int]],
    chunk_text: str,
) -> list[EvidenceSpan]:
    """Convert model sentence indices to EvidenceSpan objects.

    Filters out-of-range indices. Returns empty list if offsets empty.
    """
    if not sentence_offsets:
        return []

    spans = []
    for index in evidence_indices:
        if index < 0 or index >= len(sentence_offsets):
            continue
        start, end = sentence_offsets[index]
        text = chunk_text[start:end]
        spans.append(EvidenceSpan(
            sentence_index=index,
            text=text,
            char_start=start,
            char_end=end,
        ))
    return spans


async def verify_entity(
    entity: ExtractedEntity,
    chunk_text: str,
    sentence_offsets: list[tuple[int, int]],
    client: ExtractionLLMClient,
) -> tuple[ClaimVerdict, list[EvidenceSpan], str, bool]:
    """Verify a single entity against source text.

    Returns (verdict, evidence_spans, contradiction_reason, was_failure).
    On ExtractionLLMError: returns (INSUFFICIENT, [], "", True) — D75.
    """
    hypothesis = entity_to_hypothesis(entity)
    system_prompt = build_verification_system_prompt()
    user_prompt = build_verification_user_prompt(
        hypothesis, chunk_text, sentence_offsets
    )

    try:
        result = await client.verify(system_prompt, user_prompt, VerificationResult)
        verdict = _parse_verdict(result.verdict)
        evidence = _build_evidence_spans(
            result.evidence_sentences, sentence_offsets, chunk_text
        )
        return (verdict, evidence, result.contradiction_reason, False)
    except ExtractionLLMError:
        log.warning(
            "verification_entity_failed",
            entity_name=entity.name,
            entity_type=entity.entity_type,
        )
        return (ClaimVerdict.INSUFFICIENT, [], "", True)


async def verify_relationship(
    rel: ExtractedRelationship,
    chunk_text: str,
    sentence_offsets: list[tuple[int, int]],
    client: ExtractionLLMClient,
) -> tuple[ClaimVerdict, list[EvidenceSpan], str, bool]:
    """Verify a single relationship against source text.

    Returns (verdict, evidence_spans, contradiction_reason, was_failure).
    On ExtractionLLMError: returns (INSUFFICIENT, [], "", True) — D75.
    """
    hypothesis = relationship_to_hypothesis(rel)
    system_prompt = build_verification_system_prompt()
    user_prompt = build_verification_user_prompt(
        hypothesis, chunk_text, sentence_offsets
    )

    try:
        result = await client.verify(system_prompt, user_prompt, VerificationResult)
        verdict = _parse_verdict(result.verdict)
        evidence = _build_evidence_spans(
            result.evidence_sentences, sentence_offsets, chunk_text
        )
        return (verdict, evidence, result.contradiction_reason, False)
    except ExtractionLLMError:
        log.warning(
            "verification_relationship_failed",
            subject=rel.subject_name,
            predicate=rel.predicate,
            object=rel.object_name,
        )
        return (ClaimVerdict.INSUFFICIENT, [], "", True)


async def verify_batch(
    batch: ExtractionBatch,
    client: ExtractionLLMClient,
    config: ExtractionSettings,
) -> list[tuple[ClaimVerdict, list[EvidenceSpan], str, bool]]:
    """Verify all entities and relationships in a batch.

    Uses ``batch.chunks`` (populated by the pipeline before verification).
    Returns results in order: entities first, then relationships,
    matching batch.entities + batch.relationships order.
    Fourth tuple element is was_failure (True if ExtractionLLMError).
    """
    chunks = batch.chunks
    chunk_lookup = {chunk.chunk_id: chunk for chunk in chunks}

    semaphore = asyncio.Semaphore(config.concurrency_limit)

    def _find_chunk(chunk_source_map: list[tuple[str, int]]):
        """Find primary chunk for verification (D69a: first chunk_id)."""
        if chunk_source_map:
            primary_id = chunk_source_map[0][0]
            if primary_id in chunk_lookup:
                return chunk_lookup[primary_id]
        if chunks:
            return chunks[0]
        return None

    async def _verify_entity(entity: ExtractedEntity):
        async with semaphore:
            chunk = _find_chunk(entity.chunk_source_map)
            if chunk is None:
                return (ClaimVerdict.INSUFFICIENT, [], "", True)
            return await verify_entity(
                entity, chunk.text, chunk.sentence_offsets, client
            )

    async def _verify_rel(rel: ExtractedRelationship):
        async with semaphore:
            chunk = _find_chunk(rel.chunk_source_map)
            if chunk is None:
                return (ClaimVerdict.INSUFFICIENT, [], "", True)
            return await verify_relationship(
                rel, chunk.text, chunk.sentence_offsets, client
            )

    entity_coros = [_verify_entity(e) for e in batch.entities]
    rel_coros = [_verify_rel(r) for r in batch.relationships]

    results = await asyncio.gather(*entity_coros, *rel_coros)
    return list(results)
