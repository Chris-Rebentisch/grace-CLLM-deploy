"""Chunk 28 D213 — stable Pydantic response shapes for graph read routes.

These types are the source of truth that `frontend/lib/api/types.ts`
mirrors verbatim. `scripts/check-api-contract.sh` diffs field names
between this module and the TypeScript mirror.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EntityRecord(BaseModel):
    """A single entity vertex row surfaced to the viewer/inspector."""

    model_config = ConfigDict(extra="forbid")

    grace_id: str
    entity_type: str
    properties: dict[str, Any]
    source_document_id: str | None = None
    extraction_event_id: str | None = None
    ontology_module: str | None = None
    human_validated: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    extraction_confidence: float | None = None


class RelationshipRecord(BaseModel):
    """A single relationship (edge) row surfaced to the viewer/inspector."""

    model_config = ConfigDict(extra="forbid")

    grace_id: str
    relationship_type: str
    source_grace_id: str
    target_grace_id: str
    properties: dict[str, Any]
    source_document_id: str | None = None
    extraction_event_id: str | None = None
    ontology_module: str | None = None
    human_validated: bool = False
    extraction_confidence: float | None = None


class PagedEntitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityRecord]
    next_cursor: str | None = None


class PagedRelationshipsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationships: list[RelationshipRecord]
    next_cursor: str | None = None


class NeighborhoodResponse(BaseModel):
    """Verbatim shape returned by `fetch_entity_neighborhood`."""

    model_config = ConfigDict(extra="forbid")

    seed: dict[str, Any]
    neighbors: list[dict[str, Any]]
    edges: list[dict[str, Any]]
