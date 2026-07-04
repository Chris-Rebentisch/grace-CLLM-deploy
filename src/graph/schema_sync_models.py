"""Pydantic models for graph schema sync and index management."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class DDLStatement(BaseModel):
    """A single DDL statement with execution status."""

    statement: str = Field(description="The SQL DDL string")
    status: str = Field(default="pending", description="pending | executed | failed")
    error: str | None = Field(default=None, description="Error message if failed")
    executed_at: datetime | None = Field(default=None, description="When this statement was executed")


class GraphSchemaSyncRecord(BaseModel):
    """Records a schema sync operation."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique sync record ID")
    ontology_version_id: str = Field(description="UUID of the ontology version synced")
    ontology_version_number: int = Field(description="Version number for display")
    ddl_statements: list[DDLStatement] = Field(default_factory=list, description="All DDL executed")
    total_statements: int = Field(default=0, description="Total DDL statements")
    succeeded: int = Field(default=0, description="Count of successfully executed statements")
    failed: int = Field(default=0, description="Count of failed statements")
    status: str = Field(default="pending", description="pending | success | partial | failed")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When sync started")
    completed_at: datetime | None = Field(default=None, description="When sync completed")
    error_message: str | None = Field(default=None, description="Overall error message if sync failed")


class GraphIndexRequest(BaseModel):
    """A request from the analytics module to create an index."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique request ID")
    type_name: str = Field(description="Vertex or edge type name")
    property_name: str = Field(description="Property to index")
    index_type: str = Field(default="standard", description="standard | unique | fulltext")
    reason: str = Field(description="Why this index is needed")
    requested_by: str = Field(description="Module that requested it (e.g., 'analytics', 'retrieval')")
    query_count: int = Field(default=0, description="Observed queries that would benefit")
    status: str = Field(default="pending", description="pending | applied | rejected | failed")
    applied_at: datetime | None = Field(default=None, description="When this index was applied")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When request was created")
