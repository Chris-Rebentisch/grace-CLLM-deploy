"""SQLAlchemy ORM table and CRUD operations for Discovery processed documents."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session

from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.shared.database import Base


class ProcessedDocumentRow(Base):
    """SQLAlchemy ORM model for the processed_documents table."""

    __tablename__ = "processed_documents"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    file_path = Column(Text, nullable=False, unique=True)
    file_name = Column(Text, nullable=False)
    file_type = Column(String(20), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=True)
    modified_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=False, server_default=func.now())
    project = Column(Text, default="")
    domain = Column(Text, default="other")
    extracted_text = Column(Text, default="")
    docling_document_json = Column(JSONB, nullable=True)
    word_count = Column(Integer, default=0)
    status = Column(String(20), nullable=False, default="QUEUED")
    error_message = Column(Text, nullable=True)
    metadata_extra = Column(JSONB, default={})
    # D518 — email-origin row discrimination columns.
    origin = Column(Text, nullable=True)
    source_type = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_processed_documents_file_path", "file_path", unique=True),
        Index("ix_processed_documents_status", "status"),
        Index("ix_processed_documents_domain", "domain"),
        Index("ix_processed_documents_project", "project"),
    )


def _row_to_model(row: ProcessedDocumentRow) -> ProcessedDocument:
    """Convert a SQLAlchemy row to a Pydantic ProcessedDocument."""
    return ProcessedDocument(
        id=row.id,
        file_path=row.file_path,
        file_name=row.file_name,
        file_type=FileType(row.file_type),
        file_size_bytes=row.file_size_bytes,
        created_at=row.created_at,
        modified_at=row.modified_at,
        processed_at=row.processed_at,
        project=row.project or "",
        domain=row.domain or "other",
        extracted_text=row.extracted_text or "",
        docling_document_json=row.docling_document_json,
        word_count=row.word_count or 0,
        status=ProcessingStatus(row.status),
        error_message=row.error_message,
        metadata_extra=row.metadata_extra or {},
        origin=row.origin,
        source_type=row.source_type,
    )


def _model_to_row(doc: ProcessedDocument) -> ProcessedDocumentRow:
    """Convert a Pydantic ProcessedDocument to a SQLAlchemy row."""
    return ProcessedDocumentRow(
        id=doc.id,
        file_path=doc.file_path,
        file_name=doc.file_name,
        file_type=doc.file_type.value,
        file_size_bytes=doc.file_size_bytes,
        created_at=doc.created_at,
        modified_at=doc.modified_at,
        processed_at=doc.processed_at,
        project=doc.project,
        domain=doc.domain,
        extracted_text=doc.extracted_text,
        docling_document_json=doc.docling_document_json,
        word_count=doc.word_count,
        status=doc.status.value,
        error_message=doc.error_message,
        metadata_extra=doc.metadata_extra,
        origin=doc.origin,
        source_type=doc.source_type,
    )


def create_document(db: Session, doc: ProcessedDocument) -> ProcessedDocument:
    """Insert a new processed document record."""
    row = _model_to_row(doc)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_model(row)


def update_document(db: Session, file_path: str, doc: ProcessedDocument) -> ProcessedDocument:
    """Update an existing processed document row keyed on file_path.

    Targeted UPDATE: refreshes extracted_text, docling_document_json, word_count,
    status, and processed_at. Used by --reprocess to re-run Docling from source files
    and UPDATE in place (Chunk 62, CP5, D443).
    """
    row = (
        db.query(ProcessedDocumentRow)
        .filter(ProcessedDocumentRow.file_path == file_path)
        .first()
    )
    if row is None:
        raise ValueError(f"No document found with file_path: {file_path}")

    row.extracted_text = doc.extracted_text
    row.docling_document_json = doc.docling_document_json
    row.word_count = doc.word_count
    row.status = doc.status.value
    row.processed_at = doc.processed_at
    db.commit()
    db.refresh(row)
    return _row_to_model(row)


def get_document_by_id(db: Session, doc_id: UUID) -> ProcessedDocument | None:
    """Retrieve a document by its UUID."""
    row = db.query(ProcessedDocumentRow).filter(ProcessedDocumentRow.id == doc_id).first()
    return _row_to_model(row) if row else None


def get_document_by_path(db: Session, file_path: str) -> ProcessedDocument | None:
    """Retrieve a document by its source file path. Used to check for duplicates."""
    row = (
        db.query(ProcessedDocumentRow)
        .filter(ProcessedDocumentRow.file_path == file_path)
        .first()
    )
    return _row_to_model(row) if row else None


def list_documents(
    db: Session,
    status: ProcessingStatus | None = None,
    domain: str | None = None,
    project: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ProcessedDocument]:
    """List documents with optional filters. Supports pagination."""
    query = db.query(ProcessedDocumentRow)
    if status is not None:
        query = query.filter(ProcessedDocumentRow.status == status.value)
    if domain is not None:
        query = query.filter(ProcessedDocumentRow.domain == domain)
    if project is not None:
        query = query.filter(ProcessedDocumentRow.project == project)
    rows = query.offset(offset).limit(limit).all()
    return [_row_to_model(row) for row in rows]


def update_document_status(
    db: Session,
    doc_id: UUID,
    status: ProcessingStatus,
    error_message: str | None = None,
) -> ProcessedDocument | None:
    """Update the processing status (and optional error message) of a document."""
    row = db.query(ProcessedDocumentRow).filter(ProcessedDocumentRow.id == doc_id).first()
    if row is None:
        return None
    row.status = status.value
    row.error_message = error_message
    db.commit()
    db.refresh(row)
    return _row_to_model(row)


def get_processing_summary(db: Session) -> dict:
    """Return counts by status, by domain, by file_type, total word count."""
    # Count by status
    status_rows = (
        db.query(ProcessedDocumentRow.status, func.count())
        .group_by(ProcessedDocumentRow.status)
        .all()
    )
    by_status = {row[0]: row[1] for row in status_rows}

    # Count by domain
    domain_rows = (
        db.query(ProcessedDocumentRow.domain, func.count())
        .group_by(ProcessedDocumentRow.domain)
        .all()
    )
    by_domain = {row[0]: row[1] for row in domain_rows}

    # Count by file_type
    type_rows = (
        db.query(ProcessedDocumentRow.file_type, func.count())
        .group_by(ProcessedDocumentRow.file_type)
        .all()
    )
    by_file_type = {row[0]: row[1] for row in type_rows}

    # Total word count
    total_words = db.query(func.coalesce(func.sum(ProcessedDocumentRow.word_count), 0)).scalar()

    return {
        "by_status": by_status,
        "by_domain": by_domain,
        "by_file_type": by_file_type,
        "total_word_count": total_words,
    }
