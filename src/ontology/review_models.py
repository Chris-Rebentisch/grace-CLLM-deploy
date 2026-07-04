"""Pydantic models and enums for the Guided Review workflow."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ReviewSessionStatus(str, Enum):
    """Lifecycle status of a review session."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ReviewElementType(str, Enum):
    """What kind of schema element is being reviewed."""

    ENTITY_TYPE = "entity_type"
    RELATIONSHIP = "relationship"


class ReviewDecisionType(str, Enum):
    """What the human decided about a schema element."""

    APPROVED = "approved"
    RENAMED = "renamed"
    EDITED = "edited"
    SPLIT = "split"
    MERGED = "merged"
    REJECTED = "rejected"
    REDIRECTED = "redirected"
    RECLASSIFIED = "reclassified"
    AUTO_APPROVED = "auto_approved"


class ReviewElementStatus(str, Enum):
    """Review status of an individual schema element within a session."""

    PENDING = "pending"
    DECIDED = "decided"


class ChangeOfStatusEntityType(str, Enum):
    """What kind of entity had a status change (PKO pattern)."""

    REVIEW_SESSION = "review_session"
    SCHEMA_VERSION = "schema_version"
    SCHEMA_PROPOSAL = "schema_proposal"
    REVIEW_ELEMENT = "review_element"


class ReviewSession(BaseModel):
    """A guided review session where the human reviews a SeedSchema."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    status: ReviewSessionStatus = Field(default=ReviewSessionStatus.IN_PROGRESS)
    reviewer: str = Field(description="Who is conducting the review")
    seed_schema_merge_run_id: str = Field(
        description="Links to the SchemaMergeRun that produced the SeedSchema"
    )
    seed_schema_snapshot: dict = Field(
        description="Frozen copy of SeedSchema at review start"
    )
    total_entity_types: int = Field(
        default=0, description="Total entity types in seed schema"
    )
    total_relationships: int = Field(
        default=0, description="Total relationships in seed schema"
    )
    reviewed_entity_types: int = Field(
        default=0, description="Count of entity types with decisions"
    )
    reviewed_relationships: int = Field(
        default=0, description="Count of relationships with decisions"
    )
    resulting_version_id: UUID | None = Field(
        default=None,
        description="FK to ontology_versions. Populated on completion.",
    )
    metadata_extra: dict = Field(default_factory=dict)


class ReviewDecision(BaseModel):
    """A single human decision on a schema element during review."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(description="FK to review_sessions")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    element_type: ReviewElementType = Field(
        description="entity_type or relationship"
    )
    element_name: str = Field(
        description="Name of the type/relationship being reviewed"
    )
    decision: ReviewDecisionType = Field(
        description="What the human decided"
    )
    original_data: dict = Field(
        description="MergedEntityType or MergedRelationship as JSON from Discovery"
    )
    modified_data: dict | None = Field(
        default=None,
        description="Modified version after human changes. NULL for reject/approve-as-is.",
    )
    split_into: list[dict] | None = Field(
        default=None, description="For split: list of new type definitions"
    )
    merged_with: str | None = Field(
        default=None,
        description="For merge: name of the other type merged into this one",
    )
    reviewer: str = Field(description="Who made this decision")
    notes: str | None = Field(
        default=None, description="Optional human reasoning"
    )
    cq_impact: dict | None = Field(
        default=None,
        description="CQ coverage change snapshot: {cqs_affected, coverage_before, coverage_after}",
    )
    metadata_extra: dict = Field(default_factory=dict)


class ChangeOfStatusEvent(BaseModel):
    """A status transition event following the PKO pattern.

    Records every status change as an independent auditable entity.
    Enables queries like: 'How long did this review session spend in progress?'
    and 'Who moved this schema version from review to production?'
    """

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entity_type: ChangeOfStatusEntityType = Field(
        description="What kind of entity changed status"
    )
    entity_id: UUID = Field(
        description="ID of the entity whose status changed"
    )
    from_status: str = Field(description="Previous status value")
    to_status: str = Field(description="New status value")
    agent: str = Field(
        description="Who/what triggered the transition. Human name or 'system:auto_approve' or 'system:abandon'"
    )
    reason: str | None = Field(
        default=None, description="Why the status changed"
    )
    metadata_extra: dict = Field(default_factory=dict)
