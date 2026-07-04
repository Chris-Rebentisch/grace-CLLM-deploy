"""Pydantic request/response models for entity and relationship CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EntityCreate(BaseModel):
    """Request to insert a single entity into the graph."""

    entity_type: str = Field(description="Vertex type name, e.g. 'Legal_Entity'")
    properties: dict[str, Any] = Field(description="Domain properties from ontology")
    valid_from: datetime | None = Field(default=None, description="When this fact became valid")
    valid_to: datetime | None = Field(default=None, description="When this fact ceased to be valid")
    extraction_confidence: float | None = Field(default=None, description="0.0-1.0 confidence")
    source_document_id: str | None = Field(default=None, description="Source document UUID")
    extraction_event_id: str | None = Field(default=None, description="Extraction_Event UUID")
    schema_version: int | None = Field(default=None, description="Ontology version at extraction")
    ontology_module: str | None = Field(default=None, description="Which ontology module governed")
    human_validated: bool = Field(default=False, description="Whether human-validated")
    # Chunk 59 (D426 — CP8): evidence source origin.
    evidence_origin: Literal["document", "communication", "hybrid", "human_intent"] = Field(
        default="document",
        description="Source origin of the evidence: document, communication, hybrid, or "
        "human_intent (the intent meta-layer — reasoning elicited from a human, D-int-9).",
    )
    # D519 — access-control vertex property for privilege governance;
    # format per D344/D440; D466 Document_Chunk precedent mirrored for domain entities.
    sensitivity_tags: str = Field(
        default="",
        description="Bar-form sensitivity tags (D519, D344/D440 format)",
    )


class EntityCreateResponse(BaseModel):
    """Response after entity insertion."""

    grace_id: str = Field(description="GrACE UUID4 identifier")
    rid: str = Field(description="ArcadeDB RID like '#1:0'")
    entity_type: str = Field(description="Vertex type name")
    created: bool = Field(description="True if newly created, False if canonical match")
    canonical_match: bool = Field(description="True if returned existing entity via dedup")


class EntityUpdate(BaseModel):
    """Partial update of entity properties by grace_id."""

    properties: dict[str, Any] = Field(description="Only properties being changed")


class RelationshipCreate(BaseModel):
    """Request to insert a single relationship (edge)."""

    relationship_type: str = Field(description="Edge type name, e.g. 'owns'")
    source_grace_id: str = Field(description="grace_id of source vertex")
    target_grace_id: str = Field(description="grace_id of target vertex")
    properties: dict[str, Any] = Field(default_factory=dict, description="Edge domain properties")
    valid_from: datetime | None = Field(default=None)
    valid_to: datetime | None = Field(default=None)
    extraction_confidence: float | None = Field(default=None)
    relationship_confidence: float | None = Field(default=None)
    source_document_id: str | None = Field(default=None)
    extraction_event_id: str | None = Field(default=None)
    schema_version: int | None = Field(default=None)
    ontology_module: str | None = Field(default=None)


class RelationshipCreateResponse(BaseModel):
    """Response after relationship insertion."""

    grace_id: str = Field(description="Edge grace_id UUID4")
    relationship_type: str
    source_grace_id: str
    target_grace_id: str


class BulkInsertRequest(BaseModel):
    """Batch of entities + relationships from one Extraction event."""

    entities: list[EntityCreate] = Field(description="Entities to insert")
    relationships: list[RelationshipCreate] = Field(default_factory=list)
    extraction_event_id: str | None = Field(default=None, description="Shared across batch")
    source_document_id: str | None = Field(default=None, description="Shared across batch")


class BulkInsertResponse(BaseModel):
    """Results of bulk insert with partial success support."""

    entities_created: int = Field(default=0)
    entities_matched: int = Field(default=0, description="Canonical matches (deduped)")
    entities_failed: int = Field(default=0)
    relationships_created: int = Field(default=0)
    relationships_failed: int = Field(default=0)
    entity_results: list[EntityCreateResponse | dict] = Field(default_factory=list)
    relationship_results: list[RelationshipCreateResponse | dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list, description="Per-item error details")
