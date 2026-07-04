"""Tests for Claim model, enums, and static methods."""

import pytest

from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ClaimVerdict,
    ConstraintSeverity,
    ConstraintViolation,
    EvidenceSpan,
)


class TestClaimVerdictEnum:
    """Tests for ClaimVerdict enum."""

    def test_has_four_values(self):
        assert len(ClaimVerdict) == 4

    def test_values(self):
        assert ClaimVerdict.PENDING.value == "PENDING"
        assert ClaimVerdict.SUPPORTED.value == "SUPPORTED"
        assert ClaimVerdict.REFUTED.value == "REFUTED"
        assert ClaimVerdict.INSUFFICIENT.value == "INSUFFICIENT"


class TestClaimStatusEnum:
    """Tests for ClaimStatus enum."""

    def test_has_four_values(self):
        assert len(ClaimStatus) == 4

    def test_values(self):
        assert ClaimStatus.AUTO_ACCEPTED.value == "auto_accepted"
        assert ClaimStatus.QUARANTINED.value == "quarantined"
        assert ClaimStatus.REJECTED.value == "rejected"
        assert ClaimStatus.SUPERSEDED.value == "superseded"


class TestConstraintSeverityEnum:
    """Tests for ConstraintSeverity enum."""

    def test_has_three_values(self):
        assert len(ConstraintSeverity) == 3

    def test_values(self):
        assert ConstraintSeverity.ERROR.value == "ERROR"
        assert ConstraintSeverity.WARNING.value == "WARNING"
        assert ConstraintSeverity.INFO.value == "INFO"


class TestClaimDefaults:
    """Tests for Claim model defaults."""

    def test_minimal_claim(self):
        """Claim validates with all defaults."""
        claim = Claim()
        assert claim.claim_id  # auto-generated UUID
        assert claim.verdict == ClaimVerdict.PENDING
        assert claim.status == ClaimStatus.AUTO_ACCEPTED
        assert claim.decision_source == "pipeline"
        assert claim.confidence is None
        assert claim.evidence_spans == []
        assert claim.constraint_violations == []

    def test_fully_populated(self, sample_claim):
        """Claim validates with all fields populated."""
        assert sample_claim.entity_type == "Legal_Entity"
        assert sample_claim.subject_name == "Acme Corp"
        assert len(sample_claim.evidence_spans) == 1
        assert sample_claim.schema_version == 1


class TestComputeFingerprint:
    """Tests for Claim.compute_fingerprint static method."""

    def test_deterministic(self):
        """Same inputs produce same hash."""
        fp1 = Claim.compute_fingerprint(
            subject_name="Acme Corp",
            predicate="entity",
            object_name=None,
            properties={"jurisdiction": "Delaware"},
            evidence_texts=["Acme Corp is a Delaware corporation."],
        )
        fp2 = Claim.compute_fingerprint(
            subject_name="Acme Corp",
            predicate="entity",
            object_name=None,
            properties={"jurisdiction": "Delaware"},
            evidence_texts=["Acme Corp is a Delaware corporation."],
        )
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex digest

    def test_order_independent_properties(self):
        """Property order doesn't affect fingerprint."""
        fp1 = Claim.compute_fingerprint(
            subject_name="X",
            predicate="rel",
            object_name="Y",
            properties={"a": "1", "b": "2"},
            evidence_texts=[],
        )
        fp2 = Claim.compute_fingerprint(
            subject_name="X",
            predicate="rel",
            object_name="Y",
            properties={"b": "2", "a": "1"},
            evidence_texts=[],
        )
        assert fp1 == fp2

    def test_order_independent_evidence(self):
        """Evidence order doesn't affect fingerprint."""
        fp1 = Claim.compute_fingerprint(
            subject_name="X",
            predicate="rel",
            object_name="Y",
            properties={},
            evidence_texts=["first", "second"],
        )
        fp2 = Claim.compute_fingerprint(
            subject_name="X",
            predicate="rel",
            object_name="Y",
            properties={},
            evidence_texts=["second", "first"],
        )
        assert fp1 == fp2

    def test_different_inputs_different_hash(self):
        """Different inputs produce different hashes."""
        fp1 = Claim.compute_fingerprint("A", "rel", "B", {}, [])
        fp2 = Claim.compute_fingerprint("C", "rel", "D", {}, [])
        assert fp1 != fp2


class TestComputeExtractionUnitId:
    """Tests for Claim.compute_extraction_unit_id static method."""

    def test_deterministic(self):
        """Same inputs produce same hash."""
        uid1 = Claim.compute_extraction_unit_id("doc1", "chunk1", 1, "v1")
        uid2 = Claim.compute_extraction_unit_id("doc1", "chunk1", 1, "v1")
        assert uid1 == uid2
        assert len(uid1) == 64

    def test_different_inputs(self):
        uid1 = Claim.compute_extraction_unit_id("doc1", "chunk1", 1, "v1")
        uid2 = Claim.compute_extraction_unit_id("doc1", "chunk2", 1, "v1")
        assert uid1 != uid2


class TestEvidenceSpan:
    """Tests for EvidenceSpan model."""

    def test_required_fields(self):
        span = EvidenceSpan(
            sentence_index=0,
            text="Acme Corp is a Delaware corporation.",
        )
        assert span.sentence_index == 0
        assert span.char_start == 0
        assert span.char_end == 0


class TestClaimTimezone:
    """Tests for timezone-aware created_at."""

    def test_created_at_is_timezone_aware(self):
        """Claim().created_at.tzinfo is not None."""
        claim = Claim()
        assert claim.created_at.tzinfo is not None


class TestConstraintViolation:
    """Tests for ConstraintViolation model."""

    def test_all_fields(self):
        violation = ConstraintViolation(
            severity=ConstraintSeverity.ERROR,
            rule="invalid_entity_type",
            message="Entity type 'Foo' not in ontology",
        )
        assert violation.severity == ConstraintSeverity.ERROR
        assert violation.rule == "invalid_entity_type"
