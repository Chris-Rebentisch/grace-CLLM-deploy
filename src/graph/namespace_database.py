"""SQLAlchemy ORM table and CRUD operations for graph_namespaces."""

from datetime import datetime
from uuid import uuid4

import structlog
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.graph.management_models import GraphNamespace
from src.shared.database import Base

log = structlog.get_logger()


# --- ORM Row Class ---


class GraphNamespaceRow(Base):
    """SQLAlchemy ORM model for the graph_namespaces table."""

    __tablename__ = "graph_namespaces"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    database_name = Column(String, unique=True, nullable=False)
    description = Column(String, default="")
    parent_database = Column(String, default="grace")
    is_mother = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    sync_status = Column(String, default="never_synced")
    metadata_ = Column("metadata", JSONB, default={})
    # Chunk 51 (D402) — federation namespace columns.
    namespace_type = Column(String(10), default="child")
    label_prefix = Column(String(50), nullable=True)
    ontology_module = Column(String(100), nullable=True)
    parent_namespace_id = Column(PG_UUID(as_uuid=True), ForeignKey("graph_namespaces.id"), nullable=True)
    # F-49 readiness gate (migration f49a_ns_readiness): child namespaces do
    # NOT receive query routing until an operator flips this True — prevents
    # the register-then-global-empty-retrieval outage.
    is_ready = Column(Boolean, nullable=False, default=False, server_default="false")


# --- Row-to-Model / Model-to-Row Converters ---


def _row_to_model(row: GraphNamespaceRow) -> GraphNamespace:
    """Convert a SQLAlchemy GraphNamespaceRow to a Pydantic GraphNamespace."""
    return GraphNamespace(
        id=str(row.id),
        database_name=row.database_name,
        description=row.description or "",
        parent_database=row.parent_database or "grace",
        is_mother=row.is_mother or False,
        created_at=row.created_at,
        last_sync_at=row.last_sync_at,
        sync_status=row.sync_status or "never_synced",
        metadata=row.metadata_ or {},
        namespace_type=row.namespace_type or "child",
        label_prefix=row.label_prefix,
        ontology_module=row.ontology_module,
        parent_namespace_id=str(row.parent_namespace_id) if row.parent_namespace_id else None,
        is_ready=bool(row.is_ready),
    )


def _model_to_row(ns: GraphNamespace) -> GraphNamespaceRow:
    """Convert a Pydantic GraphNamespace to a SQLAlchemy GraphNamespaceRow."""
    return GraphNamespaceRow(
        id=ns.id,
        database_name=ns.database_name,
        description=ns.description,
        parent_database=ns.parent_database,
        is_mother=ns.is_mother,
        created_at=ns.created_at,
        last_sync_at=ns.last_sync_at,
        sync_status=ns.sync_status,
        metadata_=ns.metadata,
        namespace_type=ns.namespace_type,
        label_prefix=ns.label_prefix,
        ontology_module=ns.ontology_module,
        parent_namespace_id=ns.parent_namespace_id,
        is_ready=ns.is_ready,
    )


# --- CRUD Functions ---


def create_namespace(db: Session, namespace: GraphNamespace) -> GraphNamespace:
    """Insert a new namespace record."""
    row = _model_to_row(namespace)
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(
        "namespace_created",
        namespace_id=str(row.id),
        database_name=namespace.database_name,
    )
    return _row_to_model(row)


def list_namespaces(db: Session) -> list[GraphNamespace]:
    """List all registered namespaces."""
    rows = (
        db.query(GraphNamespaceRow)
        .order_by(GraphNamespaceRow.created_at.desc())
        .all()
    )
    return [_row_to_model(row) for row in rows]


def get_namespace_by_name(db: Session, database_name: str) -> GraphNamespace | None:
    """Retrieve a namespace by database name."""
    row = (
        db.query(GraphNamespaceRow)
        .filter(GraphNamespaceRow.database_name == database_name)
        .first()
    )
    return _row_to_model(row) if row else None


def delete_namespace(db: Session, database_name: str) -> bool:
    """Delete a namespace by database name. Returns True if found and deleted."""
    row = (
        db.query(GraphNamespaceRow)
        .filter(GraphNamespaceRow.database_name == database_name)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    log.info("namespace_deleted", database_name=database_name)
    return True


def update_sync_status(
    db: Session, database_name: str, status: str, sync_at: datetime,
) -> None:
    """Update ``sync_status`` for a namespace; advance watermark only on success (D411).

    Capture-the-why: prior behavior wrote ``last_sync_at`` for every status, which
    broke auto-detect (``syncing`` looked like a completed watermark). Chunk 53
    audit remediation — authorized deviation from prompt copy «do not edit this
    helper»: spec §8 / D411 requires ``last_sync_at`` unchanged on ``syncing``
    and ``error``, advanced only on ``synced``.
    """
    row = (
        db.query(GraphNamespaceRow)
        .filter(GraphNamespaceRow.database_name == database_name)
        .first()
    )
    if row:
        row.sync_status = status
        if status == "synced":
            row.last_sync_at = sync_at
        db.commit()
        log.info(
            "namespace_sync_updated",
            database_name=database_name,
            status=status,
        )
