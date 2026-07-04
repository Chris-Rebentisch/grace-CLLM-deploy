"""Tests for Tier 2: deterministic annotations for clusters and CQs."""

from unittest.mock import patch

import numpy as np
import pytest

from src.discovery.merge_annotations import (
    _rule_classify,
    classify_cq_types,
    compute_agreement,
    compute_cluster_quality,
    detect_coverage_gaps,
)
from src.discovery.merge_models import CQTypeClassification, CoverageGap, GapReport, HDBSCANResult


def _make_cq(text="Test question?", source="LLM_TOP_DOWN", source_pass="top_down", domain="insurance"):
    """Create a CompetencyQuestion with mocked domain validation."""
    from src.discovery.cq_models import CQSource, CompetencyQuestion

    source_enum = CQSource(source)
    with patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]):
        return CompetencyQuestion(
            canonical_text=text,
            source=source_enum,
            source_pass=source_pass,
            domain=domain,
        )


# --- compute_agreement tests ---


def test_agreement_high():
    """3 passes present -> 'high'."""
    cqs = [
        _make_cq(source_pass="top_down"),
        _make_cq(source_pass="bottom_up"),
        _make_cq(source_pass="middle_out"),
    ]
    tier, passes = compute_agreement(cqs)
    assert tier == "high"
    assert len(passes) == 3


def test_agreement_medium():
    """2 passes -> 'medium'."""
    cqs = [
        _make_cq(source_pass="top_down"),
        _make_cq(source_pass="bottom_up"),
    ]
    tier, passes = compute_agreement(cqs)
    assert tier == "medium"


def test_agreement_low():
    """1 pass -> 'low'."""
    cqs = [
        _make_cq(source_pass="top_down"),
        _make_cq(source_pass="top_down"),
    ]
    tier, passes = compute_agreement(cqs)
    assert tier == "low"


def test_agreement_gap_fill():
    """All negative_evidence -> 'gap_fill'."""
    cqs = [
        _make_cq(source="LLM_GAP_FILL", source_pass="negative_evidence"),
        _make_cq(source="LLM_GAP_FILL", source_pass="negative_evidence"),
    ]
    tier, passes = compute_agreement(cqs)
    assert tier == "gap_fill"


def test_agreement_singleton():
    """Single CQ -> 'singleton'."""
    cqs = [_make_cq(source_pass="top_down")]
    tier, passes = compute_agreement(cqs)
    assert tier == "singleton"


# --- human anchor tests ---


def test_human_anchor_detected():
    """CQ with source=HUMAN_AUTHORED detected."""
    from src.discovery.cq_models import CQSource

    cqs = [
        _make_cq(source="HUMAN_AUTHORED", source_pass="top_down"),
        _make_cq(source_pass="bottom_up"),
    ]
    has_human = any(cq.source == CQSource.HUMAN_AUTHORED for cq in cqs)
    assert has_human is True


def test_human_anchor_absent():
    """No human CQs -> False."""
    from src.discovery.cq_models import CQSource

    cqs = [
        _make_cq(source_pass="top_down"),
        _make_cq(source_pass="bottom_up"),
    ]
    has_human = any(cq.source == CQSource.HUMAN_AUTHORED for cq in cqs)
    assert has_human is False


# --- classify_domain tests ---


def test_classify_domain_basic():
    """Cluster members all same domain -> that domain via embedding centroid."""
    from src.discovery.merge_annotations import classify_domain_embedding

    np.random.seed(42)
    # Create embeddings for insurance CQs close together
    insurance_embs = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(3)]
    legal_embs = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(3)]

    all_embeddings = insurance_embs + legal_embs
    all_cqs = [_make_cq(domain="insurance") for _ in range(3)] + [_make_cq(domain="legal") for _ in range(3)]

    # Cluster is the insurance embeddings
    domain, confidence, dist = classify_domain_embedding(
        insurance_embs, all_embeddings, all_cqs, {}
    )
    assert domain == "insurance"
    assert confidence > 0.5


def test_classify_domain_cross():
    """Mixed domains -> cross_domain=True check."""
    cqs = [
        _make_cq(domain="insurance"),
        _make_cq(domain="legal"),
    ]
    unique_domains = set(cq.domain for cq in cqs)
    cross_domain = len(unique_domains) > 1
    assert cross_domain is True


# --- classify_cq_types tests ---


def test_classify_cq_types_embedding():
    """Mock similar embeddings to template, verify classification."""
    # Create a mock template with cq_type attribute
    from unittest.mock import MagicMock

    template = MagicMock()
    template.cq_type = MagicMock()
    template.cq_type.value = "SCOPING"

    cq_embs = [[1.0] * 384]
    template_embs = [[1.0] * 384]  # Identical -> high similarity

    results = classify_cq_types(
        ["What types of policies exist?"], cq_embs, template_embs, [template]
    )
    assert len(results) == 1
    assert results[0].embedding_cq_type == "SCOPING"
    assert results[0].embedding_cq_type_confidence > 0.9


def test_classify_cq_types_rule_scoping():
    """'What types of X' -> SCOPING via rule."""
    result = _rule_classify("What types of insurance policies exist?")
    assert result == "SCOPING"


def test_classify_cq_types_rule_validating():
    """'What is the X of Y' -> VALIDATING via rule."""
    result = _rule_classify("What is the expiry date of the policy?")
    assert result == "VALIDATING"


def test_classify_cq_types_rule_foundational():
    """'What are the different kinds of X' -> FOUNDATIONAL via rule."""
    result = _rule_classify("What are the different stages of a claim?")
    assert result == "FOUNDATIONAL"


def test_classify_cq_types_rule_relationship():
    """'Which X are connected to Y' -> RELATIONSHIP via rule."""
    result = _rule_classify("Which entities are connected to this policy?")
    assert result == "RELATIONSHIP"


def test_classify_cq_types_rule_metaproperty():
    """'How has X changed over time' -> METAPROPERTY via rule."""
    result = _rule_classify("How has the premium changed over time?")
    assert result == "METAPROPERTY"


def test_classify_cq_types_rule_unclassified():
    """No matching pattern -> UNCLASSIFIED."""
    result = _rule_classify("Can we optimize throughput?")
    assert result == "UNCLASSIFIED"


# --- type agreement tests ---


def test_type_agreement_match():
    """Embedding and rule agree -> type_agreement=True."""
    from unittest.mock import MagicMock

    template = MagicMock()
    template.cq_type = MagicMock()
    template.cq_type.value = "SCOPING"

    # Embed a text that matches SCOPING pattern via rule too
    cq_embs = [[1.0] * 384]
    template_embs = [[1.0] * 384]

    results = classify_cq_types(
        ["What types of policies exist?"], cq_embs, template_embs, [template]
    )
    assert results[0].type_agreement is True
    assert results[0].embedding_cq_type == "SCOPING"
    assert results[0].rule_cq_type == "SCOPING"


def test_type_agreement_mismatch():
    """Embedding and rule disagree -> type_agreement=False."""
    from unittest.mock import MagicMock

    template = MagicMock()
    template.cq_type = MagicMock()
    template.cq_type.value = "VALIDATING"

    # Text matches SCOPING rule but template says VALIDATING
    cq_embs = [[1.0] * 384]
    template_embs = [[1.0] * 384]

    results = classify_cq_types(
        ["What types of policies exist?"], cq_embs, template_embs, [template]
    )
    assert results[0].type_agreement is False
    assert results[0].embedding_cq_type == "VALIDATING"
    assert results[0].rule_cq_type == "SCOPING"


# --- coverage gap tests ---


def test_coverage_gap_domain():
    """Domain with 0 clusters flagged as critical (high severity)."""
    clusters = {0: [0, 1], 1: [2, 3]}
    singletons = [4]
    configured_domains = ["insurance", "legal", "operations"]
    config = {"min_clusters_per_domain": 2}

    report = detect_coverage_gaps(clusters, singletons, configured_domains, config)
    assert isinstance(report, GapReport)
    # All domains start at 0 count, so all should have gaps
    domain_gaps = [g for g in report.gaps if g.gap_type == "domain_gap"]
    assert len(domain_gaps) >= 1
    # At least one should be high severity (count == 0)
    high_severity = [g for g in domain_gaps if g.severity == "high"]
    assert len(high_severity) >= 1


# --- cluster quality tests ---


def test_quality_gate_clean():
    """High pairwise + high membership -> 'clean'."""
    # Build a similarity matrix where cluster members are highly similar
    sim_matrix = np.ones((4, 4)).tolist()
    hdbscan_result = HDBSCANResult(
        labels=[0, 0, 0, 0],
        probabilities=[0.95, 0.90, 0.85, 0.80],
        cluster_persistence={0: 0.9},
        n_clusters=1,
        n_noise=0,
    )

    quality = compute_cluster_quality(
        cluster_indices=[0, 1, 2, 3],
        similarity_matrix=sim_matrix,
        hdbscan_result=hdbscan_result,
        cluster_label=0,
        config={},
    )
    assert quality["quality"] == "clean"
    assert quality["min_pairwise_similarity"] >= 0.80
    assert quality["max_membership_probability"] >= 0.70


def test_quality_gate_suspect():
    """Low pairwise -> 'suspect'."""
    # Build a similarity matrix with low off-diagonal values
    n = 4
    sim = np.eye(n) * 1.0
    for i in range(n):
        for j in range(n):
            if i != j:
                sim[i][j] = 0.3  # Very low similarity
    sim_matrix = sim.tolist()

    hdbscan_result = HDBSCANResult(
        labels=[0, 0, 0, 0],
        probabilities=[0.2, 0.2, 0.2, 0.2],
        cluster_persistence={0: 0.1},
        n_clusters=1,
        n_noise=0,
    )

    quality = compute_cluster_quality(
        cluster_indices=[0, 1, 2, 3],
        similarity_matrix=sim_matrix,
        hdbscan_result=hdbscan_result,
        cluster_label=0,
        config={},
    )
    assert quality["quality"] == "suspect"
    assert quality["min_pairwise_similarity"] < 0.65
