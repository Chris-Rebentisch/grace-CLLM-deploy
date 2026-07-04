"""Tier 2: deterministic annotations for clusters and CQs.

Computes cross-pass agreement, domain classification, CQ type classification,
coverage gap detection, and cluster quality scoring.
"""

import re

import numpy as np
import structlog
from sklearn.metrics.pairwise import cosine_similarity

from src.discovery.cq_models import CQSource, CompetencyQuestion
from src.discovery.merge_models import CoverageGap, CQTypeClassification, GapReport, HDBSCANResult

logger = structlog.get_logger()

# --- Rule-based CQ type patterns ---

_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SCOPING", re.compile(r"what\s+types\s+of|what\s+kinds\s+of|what\s+areas\s+of", re.IGNORECASE)),
    ("VALIDATING", re.compile(r"(what\s+is\s+the\s+.*\s+of|who\s+is\s+the|when\s+was|current\s+status)", re.IGNORECASE)),
    ("FOUNDATIONAL", re.compile(r"what\s+are\s+the\s+different|what\s+information\s+do\s+we\s+track|what\s+stages", re.IGNORECASE)),
    ("RELATIONSHIP", re.compile(r"which\s+.*connected|what\s+.*cover|what\s+does\s+.*own|what\s+obligations", re.IGNORECASE)),
    ("METAPROPERTY", re.compile(r"how\s+has\s+.*changed|where\s+did\s+the\s+information|how\s+confident", re.IGNORECASE)),
]


def compute_agreement(
    cluster_cqs: list[CompetencyQuestion],
) -> tuple[str, list[str]]:
    """Compute cross-pass agreement tier for a cluster.

    Counts unique source_pass values (excluding negative_evidence).
    Returns (agreement_tier, list_of_passes).

    Agreement tiers:
      - high: 3 main passes present
      - medium: 2 main passes present
      - low: 1 or 0 main passes present
      - gap_fill: all members are negative_evidence or LLM_GAP_FILL
      - singleton: cluster has exactly 1 member
    """
    main_passes = {"top_down", "bottom_up", "middle_out"}

    # Collect all source passes
    all_passes = [cq.source_pass for cq in cluster_cqs if cq.source_pass]

    # Check if all members are gap-fill or negative evidence
    all_gap_or_negative = all(
        cq.source_pass == "negative_evidence" or cq.source == CQSource.LLM_GAP_FILL
        for cq in cluster_cqs
    )
    if all_gap_or_negative:
        return ("gap_fill", all_passes)

    # Singleton check
    if len(cluster_cqs) == 1:
        return ("singleton", all_passes)

    # Count unique main passes (exclude negative_evidence)
    unique_main = {p for p in all_passes if p in main_passes}
    n_main = len(unique_main)

    if n_main >= 3:
        tier = "high"
    elif n_main == 2:
        tier = "medium"
    else:
        tier = "low"

    return (tier, all_passes)


def classify_domain_embedding(
    cluster_embeddings: list[list[float]],
    all_embeddings: list[list[float]],
    all_cqs: list[CompetencyQuestion],
    config: dict,
) -> tuple[str, float, dict]:
    """Classify cluster domain using embedding centroid proximity.

    Computes centroids for each domain from all CQ embeddings, then finds
    which domain centroid the cluster centroid is closest to.

    Returns (embedding_domain, confidence, domain_distribution).
    """
    # Group embeddings by domain
    domain_embeddings: dict[str, list[list[float]]] = {}
    for cq, emb in zip(all_cqs, all_embeddings):
        if cq.domain not in domain_embeddings:
            domain_embeddings[cq.domain] = []
        domain_embeddings[cq.domain].append(emb)

    # Compute domain centroids
    domain_centroids: dict[str, np.ndarray] = {}
    for domain, embs in domain_embeddings.items():
        domain_centroids[domain] = np.mean(embs, axis=0)

    # Compute cluster centroid
    cluster_centroid = np.mean(cluster_embeddings, axis=0).reshape(1, -1)

    # Find closest domain
    best_domain = "other"
    best_similarity = -1.0
    domain_distribution: dict[str, float] = {}

    for domain, centroid in domain_centroids.items():
        sim = cosine_similarity(cluster_centroid, centroid.reshape(1, -1))[0][0]
        domain_distribution[domain] = float(sim)
        if sim > best_similarity:
            best_similarity = sim
            best_domain = domain

    logger.debug(
        "domain_classified",
        embedding_domain=best_domain,
        confidence=round(best_similarity, 4),
    )

    return (best_domain, float(best_similarity), domain_distribution)


def _rule_classify(text: str) -> str:
    """Apply rule-based pattern matching to classify a CQ type."""
    lower = text.lower()
    for cq_type, pattern in _TYPE_PATTERNS:
        if pattern.search(lower):
            return cq_type
    return "UNCLASSIFIED"


def classify_cq_types(
    cq_texts: list[str],
    cq_embeddings: list[list[float]],
    template_embeddings: list[list[float]],
    templates: list,
) -> list[CQTypeClassification]:
    """Classify CQ types using both embedding similarity and rule-based patterns.

    For each CQ:
      1. Compute cosine similarity to all template embeddings
      2. Pick the best template's cq_type (or UNCLASSIFIED if sim < 0.3)
      3. Apply rule-based pattern matching
      4. Check if both methods agree

    Args:
        cq_texts: CQ canonical texts.
        cq_embeddings: Embedding vectors for each CQ.
        template_embeddings: Embedding vectors for each CLaRO template example.
        templates: CQTemplate objects (must have .cq_type attribute).

    Returns:
        List of CQTypeClassification, one per CQ.
    """
    if not template_embeddings or not templates:
        logger.warning("classify_cq_types_no_templates", msg="No templates available")
        return [
            CQTypeClassification(
                embedding_cq_type="UNCLASSIFIED",
                embedding_cq_type_confidence=0.0,
                rule_cq_type=_rule_classify(text),
                type_agreement=False,
            )
            for text in cq_texts
        ]

    cq_arr = np.array(cq_embeddings)
    template_arr = np.array(template_embeddings)
    sim_matrix = cosine_similarity(cq_arr, template_arr)

    results: list[CQTypeClassification] = []
    for i, text in enumerate(cq_texts):
        best_idx = int(np.argmax(sim_matrix[i]))
        best_sim = float(sim_matrix[i][best_idx])

        if best_sim < 0.3:
            embedding_type = "UNCLASSIFIED"
        else:
            embedding_type = templates[best_idx].cq_type.value if hasattr(templates[best_idx].cq_type, "value") else str(templates[best_idx].cq_type)

        rule_type = _rule_classify(text)

        # Agreement: both match, or both are UNCLASSIFIED
        type_agreement = embedding_type == rule_type

        results.append(
            CQTypeClassification(
                embedding_cq_type=embedding_type,
                embedding_cq_type_confidence=best_sim,
                rule_cq_type=rule_type,
                type_agreement=type_agreement,
            )
        )

    logger.info(
        "cq_types_classified",
        total=len(results),
        agreements=sum(1 for r in results if r.type_agreement),
    )
    return results


def detect_coverage_gaps(
    clusters: dict[int, list[int]],
    singletons: list[int],
    configured_domains: list[str],
    config: dict,
) -> GapReport:
    """Detect coverage gaps across domains and CQ types.

    Checks:
      - Domain gaps: domains with too few clusters
      - Type gaps: CQ types underrepresented
      - Singleton density: domains with too many singletons

    Args:
        clusters: Mapping of cluster label to list of CQ indices.
        singletons: Indices of singleton CQs.
        configured_domains: All domain categories from discovery.yaml.
        config: cq_merge config section with thresholds.

    Returns:
        GapReport with gaps, coverage counts, and singleton density.
    """
    min_clusters_per_domain = config.get("min_clusters_per_domain", 2)
    min_type_percentage = config.get("min_type_percentage", 0.05)
    max_singleton_density = config.get("max_singleton_density", 0.5)

    gaps: list[CoverageGap] = []

    # Domain coverage: count clusters per domain (placeholder - uses cluster labels)
    domain_coverage: dict[str, int] = {d: 0 for d in configured_domains}
    # Note: actual domain assignment happens at cluster level; here we track presence
    # This will be populated by the orchestrator with actual domain info

    # Type coverage placeholder
    type_coverage: dict[str, int] = {}

    # Singleton density per domain placeholder
    singleton_density: dict[str, float] = {}

    # Domain gap detection
    for domain, count in domain_coverage.items():
        if count <= min_clusters_per_domain:
            severity = "high" if count == 0 else "medium"
            gaps.append(
                CoverageGap(
                    gap_type="domain_gap",
                    target=domain,
                    severity=severity,
                    current_value=float(count),
                    threshold=float(min_clusters_per_domain),
                    message=f"Domain '{domain}' has {count} clusters (threshold: {min_clusters_per_domain})",
                )
            )

    # Type gap detection
    total_cqs = sum(len(indices) for indices in clusters.values()) + len(singletons)
    if total_cqs > 0:
        for cq_type, count in type_coverage.items():
            pct = count / total_cqs
            if pct < min_type_percentage:
                gaps.append(
                    CoverageGap(
                        gap_type="type_gap",
                        target=cq_type,
                        severity="medium" if pct > 0 else "high",
                        current_value=pct,
                        threshold=min_type_percentage,
                        message=f"CQ type '{cq_type}' is {pct:.1%} of total (threshold: {min_type_percentage:.1%})",
                    )
                )

    # Singleton density detection
    for domain, density in singleton_density.items():
        if density > max_singleton_density:
            gaps.append(
                CoverageGap(
                    gap_type="singleton_density",
                    target=domain,
                    severity="high" if density > 0.8 else "medium",
                    current_value=density,
                    threshold=max_singleton_density,
                    message=f"Domain '{domain}' has {density:.1%} singleton density (threshold: {max_singleton_density:.1%})",
                )
            )

    logger.info(
        "coverage_gaps_detected",
        total_gaps=len(gaps),
        domain_gaps=sum(1 for g in gaps if g.gap_type == "domain_gap"),
        type_gaps=sum(1 for g in gaps if g.gap_type == "type_gap"),
        density_gaps=sum(1 for g in gaps if g.gap_type == "singleton_density"),
    )

    return GapReport(
        gaps=gaps,
        domain_coverage=domain_coverage,
        type_coverage=type_coverage,
        singleton_density=singleton_density,
    )


def compute_cluster_quality(
    cluster_indices: list[int],
    similarity_matrix: list[list[float]],
    hdbscan_result: HDBSCANResult,
    cluster_label: int,
    config: dict,
) -> dict:
    """Compute composite quality metrics for a single cluster.

    Metrics:
      - min_pairwise_similarity: lowest cosine sim between any two members
      - max_membership_probability: highest HDBSCAN membership probability
      - cluster_quality_score: persistence score from HDBSCAN
      - quality: composite flag (clean, review, suspect)

    Quality thresholds:
      - clean: min_pairwise >= 0.80 AND max_membership >= 0.70
      - suspect: min_pairwise < 0.65 OR max_membership < 0.40
      - review: everything else

    Returns dict with all quality fields.
    """
    sim_arr = np.array(similarity_matrix)

    # Singletons get perfect quality
    if len(cluster_indices) == 1:
        prob = hdbscan_result.probabilities[cluster_indices[0]] if cluster_indices[0] < len(hdbscan_result.probabilities) else 0.0
        return {
            "min_pairwise_similarity": 1.0,
            "max_membership_probability": float(prob),
            "cluster_quality_score": hdbscan_result.cluster_persistence.get(cluster_label, 0.0),
            "quality": "clean",
        }

    # Extract submatrix for cluster members
    idx = cluster_indices
    submatrix = sim_arr[np.ix_(idx, idx)]

    # Min pairwise similarity (exclude diagonal)
    np.fill_diagonal(submatrix, 2.0)  # Temporarily set diagonal high
    min_pairwise = float(np.min(submatrix))
    np.fill_diagonal(submatrix, 1.0)  # Reset

    # Max membership probability among cluster members
    member_probs = [
        hdbscan_result.probabilities[i]
        for i in cluster_indices
        if i < len(hdbscan_result.probabilities)
    ]
    max_membership = max(member_probs) if member_probs else 0.0

    # Persistence score
    persistence = hdbscan_result.cluster_persistence.get(cluster_label, 0.0)

    # Composite quality flag
    if min_pairwise >= 0.80 and max_membership >= 0.70:
        quality = "clean"
    elif min_pairwise < 0.65 or max_membership < 0.40:
        quality = "suspect"
    else:
        quality = "review"

    logger.debug(
        "cluster_quality_computed",
        cluster_label=cluster_label,
        members=len(cluster_indices),
        min_pairwise=round(min_pairwise, 4),
        max_membership=round(max_membership, 4),
        quality=quality,
    )

    return {
        "min_pairwise_similarity": min_pairwise,
        "max_membership_probability": float(max_membership),
        "cluster_quality_score": persistence,
        "quality": quality,
    }
