"""Pydantic models for the three-tier CQ merge system."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


# --- Tier 1: HDBSCAN clustering results ---


class HDBSCANResult(BaseModel):
    """Output from HDBSCAN clustering over CQ embeddings."""

    labels: list[int] = Field(description="Cluster label per CQ (-1 = noise/singleton)")
    probabilities: list[float] = Field(description="HDBSCAN membership probability per CQ")
    cluster_persistence: dict[int, float] = Field(
        default_factory=dict,
        description="Persistence (stability) score per cluster label",
    )
    soft_memberships: list[list[float]] = Field(
        default_factory=list,
        description="Soft membership vectors from HDBSCAN (may be empty if unavailable)",
    )
    n_clusters: int = Field(description="Number of clusters found (excluding noise)")
    n_noise: int = Field(description="Number of noise points (singletons)")


class Tier1Result(BaseModel):
    """Complete output from Tier 1: embeddings, similarity, and clustering."""

    embeddings: list[list[float]] = Field(
        description="384-dim embedding vectors for each CQ"
    )
    similarity_matrix: list[list[float]] = Field(
        description="N x N cosine similarity matrix"
    )
    hdbscan_result: HDBSCANResult = Field(
        description="HDBSCAN clustering output"
    )
    cluster_groups: dict[int, list[int]] = Field(
        description="Mapping from cluster label to list of CQ indices"
    )
    singleton_indices: list[int] = Field(
        description="Indices of CQs assigned to noise (-1)"
    )


# --- Tier 2: deterministic annotation models ---


class CQTypeClassification(BaseModel):
    """Dual CQ type classification: embedding-based and rule-based."""

    embedding_cq_type: str = Field(
        description="CQ type from cosine similarity to CLaRO template embeddings"
    )
    embedding_cq_type_confidence: float = Field(
        description="Cosine similarity score to best-matching template"
    )
    rule_cq_type: str = Field(
        description="CQ type from rule-based pattern matching"
    )
    type_agreement: bool = Field(
        description="Whether embedding and rule classifications agree"
    )


class CoverageGap(BaseModel):
    """A single coverage gap detected during Tier 2 analysis."""

    gap_type: str = Field(description="Category: domain_gap, type_gap, singleton_density")
    target: str = Field(description="The domain or type that has the gap")
    severity: str = Field(description="Gap severity: low, medium, high")
    current_value: float = Field(description="Current metric value")
    threshold: float = Field(description="Threshold that was violated")
    message: str = Field(description="Human-readable description of the gap")


class GapReport(BaseModel):
    """Aggregated coverage gap report from Tier 2 analysis."""

    gaps: list[CoverageGap] = Field(
        default_factory=list, description="All detected coverage gaps"
    )
    domain_coverage: dict[str, int] = Field(
        default_factory=dict, description="Number of clusters per domain"
    )
    type_coverage: dict[str, int] = Field(
        default_factory=dict, description="Number of CQs per Keet type"
    )
    singleton_density: dict[str, float] = Field(
        default_factory=dict,
        description="Ratio of singletons to total CQs per domain",
    )


# --- Tier 3: LLM call response models ---


class SplitRecommendation(BaseModel):
    """Recommendation to split a cluster member into its own CQ."""

    index: int = Field(description="Index of the CQ within the cluster")
    reason: str = Field(description="Why this CQ should be split out")


class CanonicalPhrasingItem(BaseModel):
    """Canonical phrasing result for one cluster from Call 1."""

    cluster_label: int = Field(description="Cluster label from HDBSCAN")
    canonical_text: str = Field(description="The LLM-chosen canonical phrasing")
    canonical_index: int = Field(
        description="Index of the chosen canonical CQ within the cluster"
    )
    split_recommendations: list[SplitRecommendation] = Field(
        default_factory=list,
        description="Members that should be split into separate CQs",
    )


class Call1Response(BaseModel):
    """Response from Tier 3 Call 1: canonical phrasing selection."""

    clusters: list[CanonicalPhrasingItem] = Field(
        description="Canonical phrasing for each cluster"
    )


class SubDomain(BaseModel):
    """A sub-domain within a domain group."""

    name: str = Field(description="Sub-domain name")
    cq_ids: list[str] = Field(
        description="CQ identifiers belonging to this sub-domain"
    )


class DomainGroup(BaseModel):
    """A domain grouping in the CQ hierarchy."""

    domain: str = Field(description="Domain name")
    sub_domains: list[SubDomain] = Field(
        default_factory=list, description="Sub-domains within this domain"
    )


class CrossDomainLink(BaseModel):
    """A link between CQs across different domains."""

    source_cq_id: str = Field(description="Source CQ identifier")
    target_cq_id: str = Field(description="Target CQ identifier")
    relationship: str = Field(description="Nature of the cross-domain relationship")


class Call2Response(BaseModel):
    """Response from Tier 3 Call 2: hierarchy organization."""

    domain_groups: list[DomainGroup] = Field(
        description="CQs organized into domain/sub-domain hierarchy"
    )
    cross_domain_links: list[CrossDomainLink] = Field(
        default_factory=list,
        description="Relationships that span domain boundaries",
    )


class GapFillCQ(BaseModel):
    """A gap-fill CQ proposed by the LLM in Call 3."""

    canonical_text: str = Field(description="The proposed CQ text")
    domain: str = Field(description="Target domain for this CQ")
    cq_type: str = Field(description="Keet CQ type")
    gap_addressed: str = Field(description="Which coverage gap this CQ addresses")
    rationale: str = Field(description="Why this CQ is needed")


class PathAnnotation(BaseModel):
    """An ontology path annotation for a CQ."""

    cq_id: str = Field(description="CQ identifier")
    expected_path: str = Field(
        description="Expected ontology traversal path to answer this CQ"
    )
    path_types: list[str] = Field(
        default_factory=list,
        description="Ontology types referenced in the path",
    )
    path_properties: list[str] = Field(
        default_factory=list,
        description="Ontology properties referenced in the path",
    )


class Call3Response(BaseModel):
    """Response from Tier 3 Call 3: gap analysis and path annotations."""

    gap_fill_cqs: list[GapFillCQ] = Field(
        default_factory=list, description="Proposed gap-fill CQs"
    )
    path_annotations: list[PathAnnotation] = Field(
        default_factory=list,
        description="Ontology path annotations for existing CQs",
    )


# --- Merge run tracking ---


class MergeRun(BaseModel):
    """Tracks a single execution of the three-tier CQ merge pipeline."""

    run_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this merge run",
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the merge run started",
    )
    completed_at: datetime | None = Field(
        default=None, description="When the merge run completed"
    )
    status: str = Field(
        default="running", description="Current status: running, completed, failed"
    )
    model: str = Field(default="", description="LLM model used for Tier 3 calls")
    provider: str = Field(default="", description="LLM provider used")
    total_cqs_input: int = Field(
        default=0, description="Total CQs fed into the merge pipeline"
    )
    total_clusters: int = Field(
        default=0, description="Number of clusters formed by HDBSCAN"
    )
    total_singletons: int = Field(
        default=0, description="Number of noise/singleton CQs"
    )
    total_gap_fills: int = Field(
        default=0, description="Number of gap-fill CQs generated"
    )
    canonical_count: int = Field(
        default=0,
        description="Size of the collapsed, schema-only canonical review set "
        "(the human-facing CQ count after redundant variants are folded in)",
    )
    mean_cluster_size: float = Field(
        default=0.0, description="Average number of CQs per cluster"
    )
    mean_intra_similarity: float = Field(
        default=0.0, description="Average intra-cluster cosine similarity"
    )
    agreement_distribution: dict = Field(
        default_factory=dict,
        description="Count of clusters by agreement tier (high/medium/low)",
    )
    quality_distribution: dict = Field(
        default_factory=dict,
        description="Count of clusters by quality flag (clean/review/suspect)",
    )
    hierarchy_json: dict | None = Field(
        default=None, description="Tier 3 Call 2 hierarchy output"
    )
    gap_report_json: dict | None = Field(
        default=None, description="Tier 2 coverage gap report"
    )
    tier3_results_json: dict | None = Field(
        default=None, description="Combined Tier 3 outputs"
    )
    duration_ms: int = Field(
        default=0, description="Total pipeline duration in milliseconds"
    )
    error_message: str | None = Field(
        default=None, description="Error message if the run failed"
    )
