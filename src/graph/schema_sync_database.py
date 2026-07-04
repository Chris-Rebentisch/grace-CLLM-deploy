"""SQLAlchemy ORM table and CRUD operations for graph_schema_syncs."""

from uuid import uuid4

import structlog
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.graph.schema_sync_models import DDLStatement, GraphIndexRequest, GraphSchemaSyncRecord
from src.shared.database import Base

log = structlog.get_logger()


# --- ORM Row Class ---


class GraphSchemaSyncRow(Base):
    """SQLAlchemy ORM model for the graph_schema_syncs table."""

    __tablename__ = "graph_schema_syncs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ontology_version_id = Column(PG_UUID(as_uuid=True), nullable=False)
    ontology_version_number = Column(Integer, nullable=False)
    ddl_statements = Column(JSONB, nullable=False)
    total_statements = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --- Row-to-Model / Model-to-Row Converters ---


def _row_to_model(row: GraphSchemaSyncRow) -> GraphSchemaSyncRecord:
    """Convert a SQLAlchemy GraphSchemaSyncRow to a Pydantic GraphSchemaSyncRecord."""
    return GraphSchemaSyncRecord(
        id=str(row.id),
        ontology_version_id=str(row.ontology_version_id),
        ontology_version_number=row.ontology_version_number,
        ddl_statements=[DDLStatement(**s) for s in (row.ddl_statements or [])],
        total_statements=row.total_statements,
        succeeded=row.succeeded,
        failed=row.failed,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_message=row.error_message,
    )


def _model_to_row(record: GraphSchemaSyncRecord) -> GraphSchemaSyncRow:
    """Convert a Pydantic GraphSchemaSyncRecord to a SQLAlchemy GraphSchemaSyncRow."""
    return GraphSchemaSyncRow(
        id=record.id,
        ontology_version_id=record.ontology_version_id,
        ontology_version_number=record.ontology_version_number,
        ddl_statements=[s.model_dump(mode="json") for s in record.ddl_statements],
        total_statements=record.total_statements,
        succeeded=record.succeeded,
        failed=record.failed,
        status=record.status,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error_message=record.error_message,
    )


# --- CRUD Functions ---


def create_sync_record(db: Session, record: GraphSchemaSyncRecord) -> GraphSchemaSyncRecord:
    """Insert a new sync record."""
    row = _model_to_row(record)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(
        "graph_schema_sync_created",
        sync_id=str(row.id),
        ontology_version=record.ontology_version_number,
        status=record.status,
    )
    return _row_to_model(row)


def get_sync_by_version(db: Session, ontology_version_id: str) -> GraphSchemaSyncRecord | None:
    """Retrieve the most recent sync record for a given ontology version."""
    row = (
        db.query(GraphSchemaSyncRow)
        .filter(GraphSchemaSyncRow.ontology_version_id == ontology_version_id)
        .order_by(GraphSchemaSyncRow.started_at.desc())
        .first()
    )
    return _row_to_model(row) if row else None


def get_latest_sync(db: Session) -> GraphSchemaSyncRecord | None:
    """Retrieve the most recent sync record overall."""
    row = (
        db.query(GraphSchemaSyncRow)
        .order_by(GraphSchemaSyncRow.started_at.desc())
        .first()
    )
    return _row_to_model(row) if row else None


def list_syncs(db: Session, limit: int = 50, offset: int = 0) -> list[GraphSchemaSyncRecord]:
    """List sync records ordered by started_at descending."""
    rows = (
        db.query(GraphSchemaSyncRow)
        .order_by(GraphSchemaSyncRow.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_row_to_model(row) for row in rows]


# --- ORM Row Class: Index Requests ---


class GraphIndexRequestRow(Base):
    """SQLAlchemy ORM model for the graph_index_requests table."""

    __tablename__ = "graph_index_requests"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    type_name = Column(String(255), nullable=False)
    property_name = Column(String(255), nullable=False)
    index_type = Column(String(50), nullable=False, default="standard")
    reason = Column(Text, nullable=False)
    requested_by = Column(String(100), nullable=False)
    query_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="pending")
    applied_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --- Index Request Row-to-Model / Model-to-Row ---


def _index_row_to_model(row: GraphIndexRequestRow) -> GraphIndexRequest:
    """Convert a SQLAlchemy GraphIndexRequestRow to a Pydantic GraphIndexRequest."""
    return GraphIndexRequest(
        id=str(row.id),
        type_name=row.type_name,
        property_name=row.property_name,
        index_type=row.index_type,
        reason=row.reason,
        requested_by=row.requested_by,
        query_count=row.query_count,
        status=row.status,
        applied_at=row.applied_at,
        created_at=row.created_at,
    )


def _index_model_to_row(request: GraphIndexRequest) -> GraphIndexRequestRow:
    """Convert a Pydantic GraphIndexRequest to a SQLAlchemy GraphIndexRequestRow."""
    return GraphIndexRequestRow(
        id=request.id,
        type_name=request.type_name,
        property_name=request.property_name,
        index_type=request.index_type,
        reason=request.reason,
        requested_by=request.requested_by,
        query_count=request.query_count,
        status=request.status,
        applied_at=request.applied_at,
        created_at=request.created_at,
    )


# --- Index Request CRUD ---


def create_index_request(db: Session, request: GraphIndexRequest) -> GraphIndexRequest:
    """Insert a new index request."""
    row = _index_model_to_row(request)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(
        "index_request_created",
        request_id=str(row.id),
        type_name=request.type_name,
        property_name=request.property_name,
    )
    return _index_row_to_model(row)


def get_pending_index_requests(db: Session) -> list[GraphIndexRequest]:
    """Retrieve all pending index requests."""
    rows = (
        db.query(GraphIndexRequestRow)
        .filter(GraphIndexRequestRow.status == "pending")
        .order_by(GraphIndexRequestRow.created_at.asc())
        .all()
    )
    return [_index_row_to_model(row) for row in rows]


def list_index_requests(db: Session, limit: int = 100) -> list[GraphIndexRequest]:
    """List all index requests ordered by created_at descending."""
    rows = (
        db.query(GraphIndexRequestRow)
        .order_by(GraphIndexRequestRow.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_index_row_to_model(row) for row in rows]


def update_index_request_status(db: Session, request_id: str, status: str) -> None:
    """Update the status of an index request."""
    row = db.query(GraphIndexRequestRow).filter(GraphIndexRequestRow.id == request_id).first()
    if row:
        row.status = status
        if status == "applied":
            from datetime import UTC, datetime
            row.applied_at = datetime.now(UTC)
        db.commit()
        log.info("index_request_updated", request_id=request_id, status=status)
