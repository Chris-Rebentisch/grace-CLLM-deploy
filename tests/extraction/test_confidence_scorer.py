"""Tests for confidence scoring formula."""

from src.extraction.claim_models import ClaimVerdict
from src.extraction.confidence_scorer import (
    adjust_confidence_for_verdict,
    compute_initial_confidence,
    score_claim,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity, ExtractedRelationship


def _schema_with_types():
    return {
        "entity_types": {"Legal_Entity": {}, "Contract": {}},
        "relationships": {"party_to": {}},
    }


class TestComputeInitialConfidence:
    def test_all_factors(self):
        """All factors present: score = 0.75 (cap)."""
        entity = ExtractedEntity(
            name="Acme", entity_type="Legal_Entity",
            properties={"jurisdiction": "Delaware"},
            source_sentence_indices=[0, 1],
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 2)
        assert score == 0.75

    def test_no_factors(self):
        """No factors: score = 0.0."""
        entity = ExtractedEntity(
            name="X", entity_type="Unknown_Type",
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 1)
        assert score == 0.0

    def test_source_sentences_only(self):
        """Only source sentences: 0.20."""
        entity = ExtractedEntity(
            name="X", entity_type="Unknown_Type",
            source_sentence_indices=[0],
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 1)
        assert score == 0.20

    def test_legal_type_adds(self):
        """Legal entity_type: +0.30."""
        entity = ExtractedEntity(
            name="X", entity_type="Legal_Entity",
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 1)
        assert score == 0.30

    def test_properties_add(self):
        """Has properties: +0.10."""
        entity = ExtractedEntity(
            name="X", entity_type="Unknown_Type",
            properties={"key": "val"},
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 1)
        assert score == 0.10

    def test_multi_chunk_adds(self):
        """pre_dedup_chunk_count >= 2: +0.15."""
        entity = ExtractedEntity(
            name="X", entity_type="Unknown_Type",
        )
        score = compute_initial_confidence(entity, _schema_with_types(), 2)
        assert score == 0.15

    def test_relationship_predicate_legal(self):
        """Legal predicate: +0.30."""
        rel = ExtractedRelationship(
            subject_name="A", subject_type="T",
            predicate="party_to", object_name="B", object_type="T",
        )
        score = compute_initial_confidence(rel, _schema_with_types(), 1)
        assert score == 0.30


class TestAdjustConfidenceForVerdict:
    def test_supported_floor(self):
        """SUPPORTED: always >= 0.8 with default config."""
        config = ExtractionSettings()
        result = adjust_confidence_for_verdict(0.3, ClaimVerdict.SUPPORTED, config)
        assert result >= 0.8

    def test_supported_cap(self):
        """SUPPORTED: never > 1.0."""
        config = ExtractionSettings()
        result = adjust_confidence_for_verdict(0.75, ClaimVerdict.SUPPORTED, config)
        assert result <= 1.0

    def test_insufficient_ceiling(self):
        """INSUFFICIENT: always <= 0.5 with revised default."""
        config = ExtractionSettings()
        result = adjust_confidence_for_verdict(0.75, ClaimVerdict.INSUFFICIENT, config)
        assert result <= 0.5

    def test_refuted_fixed(self):
        """REFUTED: exactly 0.05."""
        config = ExtractionSettings()
        result = adjust_confidence_for_verdict(0.75, ClaimVerdict.REFUTED, config)
        assert result == 0.05


class TestScoreClaim:
    def test_refuted_overrides_high_initial(self):
        """All factors present + REFUTED: 0.05."""
        config = ExtractionSettings()
        entity = ExtractedEntity(
            name="Acme", entity_type="Legal_Entity",
            properties={"jurisdiction": "Delaware"},
            source_sentence_indices=[0, 1],
        )
        result = score_claim(entity, ClaimVerdict.REFUTED, _schema_with_types(), config, 2)
        assert result == 0.05

    def test_supported_with_all_factors(self):
        """All factors + SUPPORTED: >= 0.8."""
        config = ExtractionSettings()
        entity = ExtractedEntity(
            name="Acme", entity_type="Legal_Entity",
            properties={"jurisdiction": "Delaware"},
            source_sentence_indices=[0],
        )
        result = score_claim(entity, ClaimVerdict.SUPPORTED, _schema_with_types(), config, 2)
        assert result >= 0.8
        assert result <= 1.0
