"""Tests for verification module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.extraction.claim_models import ClaimVerdict, EvidenceSpan
from src.extraction.extraction_models import (
    DocumentChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.instructor_client import ExtractionLLMError
from src.extraction.verification import (
    VerificationResult,
    _build_evidence_spans,
    _parse_verdict,
    verify_batch,
    verify_entity,
)


class TestParseVerdict:
    def test_supported_exact(self):
        assert _parse_verdict("SUPPORTED") == ClaimVerdict.SUPPORTED

    def test_support_variant(self):
        assert _parse_verdict("SUPPORT") == ClaimVerdict.SUPPORTED

    def test_refuted_exact(self):
        assert _parse_verdict("REFUTED") == ClaimVerdict.REFUTED

    def test_refute_variant(self):
        assert _parse_verdict("REFUTE") == ClaimVerdict.REFUTED

    def test_contradicted(self):
        assert _parse_verdict("CONTRADICTED") == ClaimVerdict.REFUTED

    def test_insufficient_exact(self):
        assert _parse_verdict("INSUFFICIENT") == ClaimVerdict.INSUFFICIENT

    def test_neutral(self):
        assert _parse_verdict("NEUTRAL") == ClaimVerdict.INSUFFICIENT

    def test_unknown_fallback(self):
        """Unrecognized value: INSUFFICIENT with warning."""
        assert _parse_verdict("MAYBE") == ClaimVerdict.INSUFFICIENT

    def test_trailing_punctuation(self):
        """Trailing period stripped before matching."""
        assert _parse_verdict("SUPPORTED.") == ClaimVerdict.SUPPORTED

    def test_unsupported_is_not_supported(self):
        """'UNSUPPORTED' is NOT in the SUPPORTED allowlist: INSUFFICIENT."""
        assert _parse_verdict("UNSUPPORTED") == ClaimVerdict.INSUFFICIENT

    def test_not_supported_is_insufficient(self):
        """'NOT SUPPORTED': INSUFFICIENT, not REFUTED."""
        assert _parse_verdict("NOT SUPPORTED") == ClaimVerdict.INSUFFICIENT

    # F-0025a / ISS-0056 regression pins: the parser was verified correct
    # during the original finding (the rubric misapplication was upstream,
    # in the judge). These pins freeze the invariant that REFUTED comes
    # ONLY from explicit-contradiction vocabulary; negation/absence
    # phrasings must land on INSUFFICIENT, never REFUTED.
    def test_not_mentioned_is_insufficient(self):
        """'NOT MENTIONED' (absence phrasing): INSUFFICIENT, not REFUTED."""
        assert _parse_verdict("NOT MENTIONED") == ClaimVerdict.INSUFFICIENT

    def test_not_refuted_is_insufficient(self):
        """'NOT REFUTED' must not substring-match into REFUTED."""
        assert _parse_verdict("NOT REFUTED") == ClaimVerdict.INSUFFICIENT

    def test_false_is_insufficient(self):
        """'FALSE' is not explicit-contradiction vocabulary: INSUFFICIENT."""
        assert _parse_verdict("FALSE") == ClaimVerdict.INSUFFICIENT

    def test_no_evidence_is_insufficient(self):
        """'NO EVIDENCE' (absence phrasing): INSUFFICIENT, not REFUTED."""
        assert _parse_verdict("NO EVIDENCE") == ClaimVerdict.INSUFFICIENT

    def test_contradicted_trailing_punctuation(self):
        """Explicit-contradiction vocabulary survives trailing period."""
        assert _parse_verdict("CONTRADICTED.") == ClaimVerdict.REFUTED

    def test_refuted_lowercase(self):
        """Case-insensitive match for the explicit-contradiction allowlist."""
        assert _parse_verdict("refuted") == ClaimVerdict.REFUTED

    def test_insufficient_lowercase_whitespace(self):
        """Case/whitespace-normalized INSUFFICIENT maps correctly."""
        assert _parse_verdict("  insufficient ") == ClaimVerdict.INSUFFICIENT


class TestBuildEvidenceSpans:
    def test_valid_indices(self):
        """Valid indices: correct EvidenceSpan objects with text."""
        text = "Acme is a company. It was founded."
        offsets = [(0, 18), (19, 34)]
        spans = _build_evidence_spans([0, 1], offsets, text)
        assert len(spans) == 2
        assert spans[0].sentence_index == 0
        assert spans[0].text == "Acme is a company."
        assert spans[0].char_start == 0
        assert spans[0].char_end == 18
        assert spans[1].sentence_index == 1
        assert spans[1].text == "It was founded."

    def test_out_of_range_filtered(self):
        """Invalid indices excluded silently."""
        text = "One sentence."
        offsets = [(0, 13)]
        spans = _build_evidence_spans([0, 5, -1], offsets, text)
        assert len(spans) == 1
        assert spans[0].sentence_index == 0

    def test_empty_offsets(self):
        """Empty sentence_offsets: empty list."""
        spans = _build_evidence_spans([0, 1], [], "some text")
        assert spans == []


class TestVerifyEntity:
    @pytest.mark.asyncio
    async def test_supported_entity(self):
        """Mocked verify() returns SUPPORTED: tuple with SUPPORTED verdict."""
        mock_result = VerificationResult(
            chain_of_thought="Acme is stated as Legal_Entity.",
            verdict="SUPPORTED",
            evidence_sentences=[0],
            contradiction_reason="",
        )
        client = MagicMock()
        client.verify = AsyncMock(return_value=mock_result)

        entity = ExtractedEntity(
            name="Acme", entity_type="Legal_Entity",
            source_sentence_indices=[0],
        )
        verdict, spans, reason, failed = await verify_entity(
            entity, "Acme is a legal entity.", [(0, 23)], client
        )
        assert verdict == ClaimVerdict.SUPPORTED
        assert len(spans) == 1
        assert reason == ""
        assert failed is False

    @pytest.mark.asyncio
    async def test_verification_failure(self):
        """ExtractionLLMError: INSUFFICIENT, empty spans, was_failure=True."""
        client = MagicMock()
        client.verify = AsyncMock(side_effect=ExtractionLLMError("fail"))

        entity = ExtractedEntity(name="X", entity_type="T")
        verdict, spans, reason, failed = await verify_entity(
            entity, "text", [(0, 4)], client
        )
        assert verdict == ClaimVerdict.INSUFFICIENT
        assert spans == []
        assert failed is True


class TestVerifyBatch:
    @pytest.mark.asyncio
    async def test_batch_verification(self):
        """Verifies all entities + relationships, returns in order."""
        mock_result = VerificationResult(
            chain_of_thought="ok",
            verdict="SUPPORTED",
            evidence_sentences=[0],
            contradiction_reason="",
        )
        client = MagicMock()
        client.verify = AsyncMock(return_value=mock_result)

        chunk = DocumentChunk(
            chunk_id="c0",
            text="Acme is a company. It signed a deal.",
            char_start=0, char_end=36,
            sentence_offsets=[(0, 18), (19, 36)],
            token_count_estimate=10,
        )

        batch = ExtractionBatch(
            document_id="doc",
            entities=[
                ExtractedEntity(
                    name="Acme", entity_type="Legal_Entity",
                    chunk_source_map=[("c0", 0)],
                ),
            ],
            relationships=[
                ExtractedRelationship(
                    subject_name="Acme", subject_type="Legal_Entity",
                    predicate="signed", object_name="Deal", object_type="Contract",
                    chunk_source_map=[("c0", 1)],
                ),
            ],
            chunks=[chunk],
        )

        config = ExtractionSettings()
        results = await verify_batch(batch, client, config)

        assert len(results) == 2
        # Entity result
        assert results[0][0] == ClaimVerdict.SUPPORTED
        assert results[0][3] is False
        # Relationship result
        assert results[1][0] == ClaimVerdict.SUPPORTED
        assert results[1][3] is False

    @pytest.mark.asyncio
    async def test_batch_empty(self):
        """Empty batch returns empty results."""
        client = MagicMock()
        batch = ExtractionBatch(
            document_id="doc", entities=[], relationships=[], chunks=[],
        )
        config = ExtractionSettings()
        results = await verify_batch(batch, client, config)
        assert results == []
