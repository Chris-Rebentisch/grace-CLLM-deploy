"""Competency question data models, enums, and cluster model."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from src.discovery.models import get_valid_domains


class CQSource(str, Enum):
    """Tracks where a CQ came from."""

    HUMAN_AUTHORED = "HUMAN_AUTHORED"
    LLM_TOP_DOWN = "LLM_TOP_DOWN"
    LLM_BOTTOM_UP = "LLM_BOTTOM_UP"
    LLM_MIDDLE_OUT = "LLM_MIDDLE_OUT"
    LLM_GAP_FILL = "LLM_GAP_FILL"
    LLM_COMBINED = "LLM_COMBINED"
    SYSTEM_GENERATED = "SYSTEM_GENERATED"


class CQType(str, Enum):
    """Keet's five-type CQ taxonomy."""

    SCOPING = "SCOPING"
    VALIDATING = "VALIDATING"
    FOUNDATIONAL = "FOUNDATIONAL"
    RELATIONSHIP = "RELATIONSHIP"
    METAPROPERTY = "METAPROPERTY"
    UNCLASSIFIED = "UNCLASSIFIED"


class CQStatus(str, Enum):
    """Lifecycle status."""

    DRAFT = "DRAFT"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class CQVerificationStatus(str, Enum):
    """Ontology verification result (OE-Assist pattern)."""

    UNTESTED = "UNTESTED"
    PASS = "PASS"
    FAIL_MISSING_TYPE = "FAIL_MISSING_TYPE"
    FAIL_MISSING_PROPERTY = "FAIL_MISSING_PROPERTY"
    FAIL_MISSING_CONNECTION = "FAIL_MISSING_CONNECTION"
    PARTIAL = "PARTIAL"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    HUMAN_OVERRIDDEN = "HUMAN_OVERRIDDEN"


class CQPriority(str, Enum):
    """User-assigned importance."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNSET = "UNSET"


class CompetencyQuestion(BaseModel):
    """A competency question that the ontology must be able to answer."""

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Unique CQ identifier")

    # Content
    canonical_text: str = Field(description="The canonical phrasing of this CQ")
    raw_user_input: str | None = Field(
        default=None,
        description="Original free-text from the user before template refinement. Preserves intent.",
    )

    # Classification (Keet taxonomy)
    cq_type: CQType = Field(
        default=CQType.UNCLASSIFIED,
        description="CQ type per Keet's taxonomy: Scoping, Validating, Foundational, Relationship, Metaproperty",
    )

    # Domain and Priority
    domain: str = Field(default="other", description="Domain category from discovery.yaml")
    priority: CQPriority = Field(
        default=CQPriority.UNSET, description="User-assigned importance"
    )

    # Provenance
    source: CQSource = Field(description="How this CQ was generated")
    source_pass: str | None = Field(
        default=None,
        description="Which generation pass produced this CQ (top_down, bottom_up, middle_out)",
    )
    template_id: str | None = Field(
        default=None,
        description="CLaRO template ID used to formalize this CQ, if any",
    )

    # Lifecycle
    status: CQStatus = Field(default=CQStatus.DRAFT, description="Current lifecycle status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this CQ was created",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this CQ was last modified",
    )

    # Version tracking
    version: int = Field(default=1, description="Version number. Increments on edit.")
    previous_text: str | None = Field(
        default=None,
        description="Previous canonical text before last edit. Supports audit trail.",
    )

    # Confidence — dual tracking (generation + verification)
    generation_confidence: float = Field(
        default=0.0,
        description="Confidence from cross-pass agreement during merge (0.0-1.0)",
    )
    verification_confidence: float = Field(
        default=0.0,
        description="Confidence from LLM path verification against ontology (0.0-1.0)",
    )

    # Ontology verification (OE-Assist pattern)
    verification_status: CQVerificationStatus = Field(
        default=CQVerificationStatus.UNTESTED,
        description="Whether this CQ is answerable by the current ontology",
    )
    verification_path: str | None = Field(
        default=None,
        description="The ontology path that answers this CQ",
    )
    verification_gap: str | None = Field(
        default=None,
        description="What is missing if the CQ fails verification",
    )

    # Linkages
    linked_document_ids: list[UUID] = Field(
        default_factory=list,
        description="ProcessedDocument IDs that are evidence for this CQ",
    )
    cluster_id: UUID | None = Field(
        default=None,
        description="Semantic cluster this CQ belongs to (from merge step)",
    )

    # Tier 2 fields (embedding-based classification)
    embedding_cq_type: str = Field(
        default="UNCLASSIFIED",
        description="CQ type from embedding classification against CLaRO templates",
    )
    embedding_cq_type_confidence: float = Field(
        default=0.0,
        description="Cosine similarity to nearest template exemplar",
    )
    rule_cq_type: str = Field(
        default="UNCLASSIFIED",
        description="CQ type from rule-based pattern matching",
    )
    type_agreement: bool = Field(
        default=False,
        description="Whether embedding and rule classifications agree",
    )

    # Metadata
    metadata_extra: dict = Field(
        default_factory=dict, description="Additional metadata (JSONB)"
    )

    @model_validator(mode="after")
    def validate_domain(self) -> "CompetencyQuestion":
        """Validate that domain is in the configured domain categories."""
        valid = get_valid_domains()
        if self.domain not in valid:
            raise ValueError(
                f"Invalid domain '{self.domain}'. Must be one of: {valid}"
            )
        return self

    @model_validator(mode="after")
    def set_human_confidence(self) -> "CompetencyQuestion":
        """Human-authored CQs get generation_confidence=1.0."""
        if self.source == CQSource.HUMAN_AUTHORED and self.generation_confidence == 0.0:
            self.generation_confidence = 1.0
        return self


class CQCluster(BaseModel):
    """A semantic cluster of equivalent competency questions."""

    id: UUID = Field(default_factory=uuid4, description="Cluster identifier")
    canonical_cq_id: UUID | None = Field(
        default=None,
        description="The CQ chosen as the canonical representative of this cluster",
    )
    domain: str = Field(default="other", description="Domain category")
    agreement_tier: str = Field(
        default="low",
        description="Cross-pass agreement: high (3/3), medium (2/3), low (1/3)",
    )
    source_passes: list[str] = Field(
        default_factory=list,
        description="Which passes contributed CQs to this cluster",
    )
    similarity_score: float = Field(
        default=0.0,
        description="Average pairwise similarity within the cluster",
    )
    member_count: int = Field(
        default=0, description="Number of CQs in this cluster"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Tier 1 fields (HDBSCAN output)
    cluster_quality_score: float = Field(
        default=0.0, description="HDBSCAN persistence/stability score"
    )
    max_membership_probability: float = Field(
        default=0.0, description="Highest HDBSCAN soft membership among members"
    )
    min_pairwise_similarity: float = Field(
        default=0.0, description="Lowest cosine similarity between any two members"
    )

    # Tier 2 fields (deterministic annotation)
    quality: str = Field(
        default="review", description="Composite quality flag: clean, review, suspect"
    )
    cross_domain: bool = Field(
        default=False, description="Members span multiple domains"
    )
    domain_distribution: dict = Field(
        default_factory=dict, description="Domain breakdown"
    )
    has_human_anchor: bool = Field(
        default=False, description="Cluster contains a human-authored CQ"
    )
    cq_type_distribution: dict = Field(
        default_factory=dict, description="Keet type breakdown"
    )
    embedding_domain: str = Field(
        default="other", description="Domain from embedding centroid classification"
    )
    embedding_domain_confidence: float = Field(
        default=0.0, description="Cosine similarity to domain centroid"
    )
