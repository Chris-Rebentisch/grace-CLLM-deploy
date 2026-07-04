"""Integration tests for Discovery database CRUD operations."""

import pytest
from sqlalchemy import text

from src.discovery.database import (
    ProcessedDocumentRow,
    create_document,
    get_document_by_id,
    get_document_by_path,
    get_processing_summary,
    list_documents,
    update_document_status,
)
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.shared.database import Base, get_db, get_engine


@pytest.fixture(autouse=True)
def clean_table():
    """Clean the processed_documents table before and after each test."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM processed_documents"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM processed_documents"))
        conn.commit()


@pytest.fixture()
def db_session():
    """Yield a database session for testing."""
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _make_doc(**overrides) -> ProcessedDocument:
    """Create a ProcessedDocument with sensible defaults."""
    defaults = {
        "file_path": "/tmp/test.pdf",
        "file_name": "test.pdf",
        "file_type": FileType.PDF,
        "file_size_bytes": 1024,
        "domain": "other",
    }
    defaults.update(overrides)
    return ProcessedDocument(**defaults)


def test_create_and_retrieve_document(db_session):
    """Insert a ProcessedDocument, retrieve by ID, verify all fields match."""
    doc = _make_doc()
    created = create_document(db_session, doc)
    assert created.id == doc.id

    retrieved = get_document_by_id(db_session, doc.id)
    assert retrieved is not None
    assert retrieved.file_path == doc.file_path
    assert retrieved.file_name == doc.file_name
    assert retrieved.file_type == doc.file_type
    assert retrieved.file_size_bytes == doc.file_size_bytes
    assert retrieved.status == ProcessingStatus.QUEUED


def test_duplicate_file_path_rejected(db_session):
    """Inserting two documents with the same file_path raises an integrity error."""
    doc1 = _make_doc()
    create_document(db_session, doc1)

    doc2 = _make_doc()  # same file_path
    with pytest.raises(Exception):  # IntegrityError
        create_document(db_session, doc2)


def test_list_documents_with_filters(db_session):
    """Insert 3 documents with different statuses and domains, verify filtering works."""
    doc1 = _make_doc(
        file_path="/tmp/a.pdf", file_name="a.pdf",
        domain="legal", status=ProcessingStatus.COMPLETE,
    )
    doc2 = _make_doc(
        file_path="/tmp/b.docx", file_name="b.docx",
        file_type=FileType.DOCX, domain="tax", status=ProcessingStatus.QUEUED,
    )
    doc3 = _make_doc(
        file_path="/tmp/c.xlsx", file_name="c.xlsx",
        file_type=FileType.XLSX, domain="legal", status=ProcessingStatus.FAILED,
    )
    for doc in [doc1, doc2, doc3]:
        create_document(db_session, doc)

    # Filter by status
    complete = list_documents(db_session, status=ProcessingStatus.COMPLETE)
    assert len(complete) == 1
    assert complete[0].file_name == "a.pdf"

    # Filter by domain
    legal = list_documents(db_session, domain="legal")
    assert len(legal) == 2

    # No filter
    all_docs = list_documents(db_session)
    assert len(all_docs) == 3


def test_processing_summary(db_session):
    """Insert documents with mixed statuses, verify get_processing_summary() returns correct counts."""
    doc1 = _make_doc(
        file_path="/tmp/a.pdf", file_name="a.pdf",
        status=ProcessingStatus.COMPLETE, word_count=100,
    )
    doc2 = _make_doc(
        file_path="/tmp/b.pdf", file_name="b.pdf",
        status=ProcessingStatus.COMPLETE, word_count=200,
    )
    doc3 = _make_doc(
        file_path="/tmp/c.pdf", file_name="c.pdf",
        status=ProcessingStatus.FAILED,
    )
    for doc in [doc1, doc2, doc3]:
        create_document(db_session, doc)

    summary = get_processing_summary(db_session)
    assert summary["by_status"]["COMPLETE"] == 2
    assert summary["by_status"]["FAILED"] == 1
    assert summary["total_word_count"] == 300


def test_update_document_status(db_session):
    """Update status and verify the change persists."""
    doc = _make_doc()
    create_document(db_session, doc)

    updated = update_document_status(
        db_session, doc.id, ProcessingStatus.FAILED, error_message="test error"
    )
    assert updated is not None
    assert updated.status == ProcessingStatus.FAILED
    assert updated.error_message == "test error"


def test_get_document_by_path(db_session):
    """Retrieve a document by file_path."""
    doc = _make_doc()
    create_document(db_session, doc)

    retrieved = get_document_by_path(db_session, "/tmp/test.pdf")
    assert retrieved is not None
    assert retrieved.id == doc.id
