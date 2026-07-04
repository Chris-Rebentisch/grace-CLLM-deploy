"""Pydantic models for graph management operations.

Covers orphan detection, health metrics, temporal windowing,
namespace management, and duplicate entity detection.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class OrphanEntity(BaseModel):
    """An entity with zero edges."""

    grace_id: str
    entity_type: str
    name: str
    created_at: datetime | None = None


class OrphanReport(BaseModel):
    """Result of orphan detection scan."""

    orphan_count: int
    total_entities: int
    orphan_rate: float = Field(description="orphan_count / total_entities")
    orphans: list[OrphanEntity]


class TypeCount(BaseModel):
    """Count of entities or relationships by type."""

    type_name: str
    count: int


class GraphHealthReport(BaseModel):
    """Aggregate graph health statistics."""

    total_vertices: int
    total_edges: int
    density: float = Field(description="total_edges / total_vertices, 0.0 if no vertices")
    orphan_count: int
    orphan_rate: float
    avg_edges_per_vertex: float
    vertex_types: list[TypeCount]
    edge_types: list[TypeCount]
    deprecated_vertices: int
    deprecated_edges: int


class TemporalWindowRequest(BaseModel):
    """Request for a temporal windowed view of the graph."""

    start: datetime = Field(description="Window start (valid_from >= start)")
    end: datetime = Field(description="Window end (valid_to <= end or NULL)")
    entity_types: list[str] = Field(default_factory=list, description="Filter to these types")
    include_relationships: bool = Field(default=True)
    limit: int = Field(default=100, ge=1, le=1000)


class TemporalWindowResponse(BaseModel):
    """Entities and relationships within a temporal window."""

    window_start: datetime
    window_end: datetime
    entities: list[dict]
    relationships: list[dict]
    entity_count: int
    relationship_count: int


class GraphNamespace(BaseModel):
    """Registry entry for a federated child graph database."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    database_name: str = Field(description="ArcadeDB database name")
    description: str = Field(default="")
    parent_database: str = Field(default="grace", description="Mother graph database")
    is_mother: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_sync_at: datetime | None = Field(default=None)
    sync_status: str = Field(
        default="never_synced",
        description="never_synced|syncing|synced|error",
    )
    metadata: dict = Field(default_factory=dict)
    # Chunk 51 (D402) — federation namespace columns.
    namespace_type: Literal["mother", "child"] = Field(
        default="child", description="Federation namespace type"
    )
    label_prefix: str | None = Field(
        default=None, description="PascalCase prefix for label namespacing"
    )
    ontology_module: str | None = Field(
        default=None, description="Ontology module this namespace is scoped to"
    )
    parent_namespace_id: str | None = Field(
        default=None, description="FK to parent namespace"
    )
    # F-49 readiness gate: query routing includes this namespace only when True.
    # Child namespaces register not-ready; the operator enables them explicitly
    # once their retrieval indexes/data exist (PATCH /api/federation/namespaces/{id}).
    is_ready: bool = Field(
        default=False,
        description="Namespace participates in federated query routing only when True",
    )


class DuplicateCandidate(BaseModel):
    """A pair of potentially duplicate entities."""

    entity_a_grace_id: str
    entity_b_grace_id: str
    entity_type: str
    name: str
    match_type: str = Field(
        default="exact_name",
        description="exact_name | embedding_similarity",
    )
    similarity_score: float | None = Field(
        default=None,
        description="Cosine similarity score for embedding_similarity matches. "
                    "None for exact_name matches.",
    )


class DuplicateReport(BaseModel):
    """Result of duplicate detection scan."""

    total_candidates: int
    by_type: dict[str, int] = Field(description="Duplicate count per entity type")
    candidates: list[DuplicateCandidate]
