"""Pydantic models for the federation infrastructure layer (Chunk 51).

Covers federation configuration, bridge edges, canonical entities,
and namespace registration request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FederationConfig(BaseModel):
    """Configuration loaded from ``config/federation.yaml``.

    Controls layer-selective sharing rules, embedding parameters,
    and label prefix conventions for the federation infrastructure.
    """

    shared_layers: list[str] = Field(
        default_factory=lambda: ["domain", "temporal"],
        description="Graph layers shared across federation boundary",
    )
    siloed_layers: list[str] = Field(
        default_factory=lambda: ["provenance", "governance"],
        description="Graph layers kept private to the originating namespace",
    )
    provenance_surface_properties: list[str] = Field(
        default_factory=lambda: [
            "source_document_id",
            "extraction_date",
            "last_updated",
            "human_reviewed",
        ],
        description="D403: curated provenance properties that cross the federation boundary",
    )
    embedding_similarity_threshold: float = Field(
        default=0.85,
        description="Cosine similarity threshold for embedding-based entity resolution",
    )
    label_prefix_convention: str = Field(
        default="PascalCase",
        description="Naming convention for label prefixes",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama base URL for embedding generation",
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description="Embedding model name for entity resolution",
    )


class BridgeEntityEdge(BaseModel):
    """Bridge edge linking a canonical entity to a child-graph entity.

    Corresponds to ``Bridge_Entity`` in ``META_EDGE_TYPES``.
    """

    grace_id: str = Field(description="Unique edge identifier")
    canonical_grace_id: str = Field(description="Canonical entity grace_id")
    child_grace_id: str = Field(description="Child-graph entity grace_id")
    namespace: str = Field(description="Originating namespace")
    resolution_method: str = Field(
        description="Method used: exact, embedding, or llm"
    )
    resolved_at: datetime = Field(description="Timestamp of resolution")


class CrossSystemReferenceEdge(BaseModel):
    """Reference edge between entities across system boundaries.

    Corresponds to ``Cross_System_Reference`` in ``META_EDGE_TYPES``.
    """

    grace_id: str = Field(description="Unique edge identifier")
    relationship_type: str = Field(description="Semantic relationship type")
    confidence_band: Literal["high", "medium", "low"] = Field(
        description="D120/D217 compliant confidence band label"
    )
    evidence_source: str = Field(description="Source of the reference evidence")
    created_at: datetime = Field(description="Timestamp of creation")


class CanonicalEntity(BaseModel):
    """A canonical entity in the entity resolution registry.

    Mirrors the ``entity_resolution_registry`` table columns.
    """

    id: UUID | None = Field(default=None, description="Registry row ID")
    canonical_grace_id: UUID = Field(description="Grace ID of the canonical entity")
    canonical_name: str = Field(description="Display name of the canonical entity")
    canonical_type: str = Field(description="Entity type name")
    aliases: dict = Field(
        default_factory=dict,
        description="Known aliases as a JSON object",
    )
    embedding_vector: list[float] | None = Field(
        default=None,
        description="Embedding vector as JSON array (no pgvector — D404)",
    )
    namespace_source: str | None = Field(
        default=None,
        description="Originating namespace, if known",
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")


class NamespaceRegistration(BaseModel):
    """Request model for federation namespace registration."""

    database_name: str = Field(description="ArcadeDB database name")
    namespace_type: Literal["mother", "child"] = Field(
        default="child",
        description="Federation namespace type",
    )
    label_prefix: str | None = Field(
        default=None,
        description="PascalCase prefix for label namespacing",
    )
    ontology_module: str | None = Field(
        default=None,
        description="Ontology module this namespace is scoped to",
    )
    parent_namespace_id: str | None = Field(
        default=None,
        description="UUID of the parent namespace",
    )
    description: str = Field(default="", description="Human-readable description")
