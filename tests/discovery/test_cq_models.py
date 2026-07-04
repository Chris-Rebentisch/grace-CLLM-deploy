"""Tests for CQ data models and enums."""

import pytest
from pydantic import ValidationError

from src.discovery.cq_models import (
    CQCluster,
    CQPriority,
    CQSource,
    CQStatus,
    CQType,
    CQVerificationStatus,
    CompetencyQuestion,
)


def test_competency_question_defaults():
    """CQ with required fields has correct defaults."""
    cq = CompetencyQuestion(
        canonical_text="What types of insurance policies exist?",
        source=CQSource.LLM_TOP_DOWN,
    )
    assert cq.status == CQStatus.DRAFT
    assert cq.priority == CQPriority.UNSET
    assert cq.verification_status == CQVerificationStatus.UNTESTED
    assert cq.version == 1
    assert cq.cq_type == CQType.UNCLASSIFIED
    assert cq.domain == "other"
    assert cq.linked_document_ids == []
    assert cq.cluster_id is None
    assert cq.id is not None


def test_cq_source_enum():
    """All CQSource values are valid."""
    expected = {
        "HUMAN_AUTHORED", "LLM_TOP_DOWN", "LLM_BOTTOM_UP",
        "LLM_MIDDLE_OUT", "LLM_GAP_FILL", "LLM_COMBINED", "SYSTEM_GENERATED",
    }
    assert {s.value for s in CQSource} == expected


def test_cq_type_enum():
    """All CQType values match Keet's taxonomy."""
    expected = {
        "SCOPING", "VALIDATING", "FOUNDATIONAL",
        "RELATIONSHIP", "METAPROPERTY", "UNCLASSIFIED",
    }
    assert {t.value for t in CQType} == expected


def test_cq_status_enum():
    """All lifecycle statuses are valid."""
    expected = {"DRAFT", "ACCEPTED", "EDITED", "REJECTED", "OUT_OF_SCOPE"}
    assert {s.value for s in CQStatus} == expected


def test_cq_verification_status_enum():
    """All verification statuses are valid."""
    expected = {
        "UNTESTED", "PASS", "FAIL_MISSING_TYPE", "FAIL_MISSING_PROPERTY",
        "FAIL_MISSING_CONNECTION", "PARTIAL", "HUMAN_CONFIRMED", "HUMAN_OVERRIDDEN",
    }
    assert {v.value for v in CQVerificationStatus} == expected


def test_cq_domain_validation():
    """Domain validated against discovery.yaml."""
    # Valid domain
    cq = CompetencyQuestion(
        canonical_text="Test?",
        source=CQSource.HUMAN_AUTHORED,
        domain="legal",
    )
    assert cq.domain == "legal"

    # Invalid domain
    with pytest.raises(ValidationError, match="Invalid domain"):
        CompetencyQuestion(
            canonical_text="Test?",
            source=CQSource.HUMAN_AUTHORED,
            domain="nonexistent_domain",
        )


def test_human_authored_confidence():
    """CQ with source=HUMAN_AUTHORED gets generation_confidence=1.0."""
    cq = CompetencyQuestion(
        canonical_text="What insurance policies exist?",
        source=CQSource.HUMAN_AUTHORED,
    )
    assert cq.generation_confidence == 1.0


def test_cq_cluster_defaults():
    """CQCluster with required fields has correct defaults."""
    cluster = CQCluster()
    assert cluster.canonical_cq_id is None
    assert cluster.domain == "other"
    assert cluster.agreement_tier == "low"
    assert cluster.source_passes == []
    assert cluster.similarity_score == 0.0
    assert cluster.member_count == 0
    assert cluster.id is not None
