"""Pydantic v2 models for the connector framework.

Source of truth for all data structures shared across the connector
ecosystem: ABC, registry, synthetic connector, schema mapper, entity
resolver, and sync pipeline CLI.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ConnectorConfig(BaseModel):
    """Configuration passed to a connector instance at construction time."""

    connector_type: str = Field(description="Registered connector type string")
    namespace_id: UUID = Field(description="Target federation namespace UUID")
    config_overrides: dict = Field(
        default_factory=dict,
        description="Connector-specific overrides (seed, scale_factor, etc.)",
    )


class ConnectorRelationship(BaseModel):
    """Relationship edge data produced by a connector record."""

    target_record_id: str = Field(description="Source-system record ID of the target entity")
    relationship_type: str = Field(description="Edge type name (PascalCase)")
    properties: dict = Field(
        default_factory=dict,
        description="Arbitrary edge properties",
    )


class ConnectorRecord(BaseModel):
    """A single entity record yielded by a connector during load/sync."""

    source_record_id: str = Field(description="Unique ID in the source system")
    entity_type: str = Field(description="PascalCase entity type name")
    name: str = Field(description="Human-readable entity name")
    properties: dict = Field(
        default_factory=dict,
        description="Arbitrary entity properties",
    )
    source_system: str = Field(description="Source system identifier (e.g. SyntheticA)")
    source_updated_at: datetime = Field(description="Last modification time in source")
    relationships: list[ConnectorRelationship] = Field(
        default_factory=list,
        description="Outbound relationships from this entity",
    )


class ConnectorSyncTriggerRequest(BaseModel):
    """Request body for ``POST /api/connectors/{connector_type}/sync`` (Chunk 53 §7.1)."""

    namespace_id: UUID = Field(description="Target graph namespace UUID")
    mode: Literal["initial", "incremental"] | None = Field(
        default=None,
        description="Sync mode; omit for auto-detect (D411).",
    )
    dry_run: bool = Field(
        default=False,
        description="When true, CLI runs with --dry-run (no persistence).",
    )
    batch_size: int = Field(
        default=100,
        ge=1,
        description="Batch size forwarded to the sync CLI.",
    )


class ConnectorHealthStatus(BaseModel):
    """Health-check result returned by a connector's health_check() method.

    Status uses ternary label only (D120/D217 — no raw numerals).
    """

    status: Literal["healthy", "degraded", "unavailable"] = Field(
        description="Ternary health label"
    )
    detail: str | None = Field(
        default=None, description="Human-readable detail string"
    )
    checked_at: datetime = Field(description="Timestamp of health check")


class ResolvedEntity(BaseModel):
    """Result of entity resolution for a single connector record."""

    outcome: Literal["bridged", "created", "updated", "queued"] = Field(
        description="Resolution outcome"
    )
    grace_id: str | None = Field(
        default=None, description="The child entity grace_id (created or existing)"
    )
    canonical_grace_id: str | None = Field(
        default=None,
        description="The canonical (mother) entity grace_id when bridged",
    )


class SyncStatus(str, enum.Enum):
    """Pipeline status for a connector sync run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncResult(BaseModel):
    """Summary of a connector sync run."""

    connector_type: str = Field(description="Connector type that was synced")
    namespace_id: UUID = Field(description="Target namespace UUID")
    status: SyncStatus = Field(description="Pipeline outcome status")
    records_processed: int = Field(default=0, description="Total records processed")
    records_bridged: int = Field(default=0, description="Records auto-bridged")
    records_created: int = Field(default=0, description="New child entities created")
    records_queued: int = Field(default=0, description="Records sent to review queue")
    # F-032a / ISS-0023: `records_queued: 90` on a completed sync looked like
    # lost records — nothing named the append-only destination table or the
    # next step. These two fields make the result self-explanatory.
    records_queued_to: str | None = Field(
        default=None,
        description="Destination of queued records (set when records_queued > 0)",
    )
    records_queued_hint: str | None = Field(
        default=None,
        description="Operator next-step hint for reviewing queued records",
    )
    records_updated: int = Field(default=0, description="Records updated in place")
    error_detail: str | None = Field(default=None, description="Error message if failed")
    started_at: datetime | None = Field(default=None, description="Run start time")
    completed_at: datetime | None = Field(default=None, description="Run end time")
