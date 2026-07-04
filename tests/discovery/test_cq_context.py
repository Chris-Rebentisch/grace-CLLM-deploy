"""Tests for CQ context reinstatement module."""

import pytest
from sqlalchemy import text

from src.discovery.cq_context import (
    extract_key_terms,
    generate_context_summary,
    generate_domain_context,
)
from src.discovery.database import ProcessedDocumentRow, create_document
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.shared.database import get_db, get_engine


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean processed_documents before and after each test."""
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
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _insert_doc(db, file_path, domain="other", word_count=100, text_content="sample text"):
    """Insert a test document."""
    doc = ProcessedDocument(
        file_path=file_path,
        file_name=file_path.split("/")[-1],
        file_type=FileType.PDF,
        file_size_bytes=1024,
        domain=domain,
        word_count=word_count,
        extracted_text=text_content,
        status=ProcessingStatus.COMPLETE,
    )
    return create_document(db, doc)


def test_generate_context_summary(db_session):
    """Returns dict with total_documents, domains, business_areas."""
    _insert_doc(db_session, "/tmp/a.pdf", "insurance", 500, "insurance policy coverage premium expiry")
    _insert_doc(db_session, "/tmp/b.pdf", "legal", 300, "contract agreement terms legal binding")
    _insert_doc(db_session, "/tmp/c.pdf", "legal", 200, "lawsuit dispute resolution legal proceedings")

    summary = generate_context_summary(db_session)
    assert summary["total_documents"] == 3
    assert summary["total_words"] == 1000
    assert "insurance" in summary["domains"]
    assert "legal" in summary["domains"]
    assert len(summary["business_areas"]) == 2
    assert "cta_prompt" in summary
    assert "2" in summary["cta_prompt"]  # "span 2 business areas"


def test_generate_domain_context(db_session):
    """Returns dict with documents, key_terms, suggested_templates."""
    _insert_doc(
        db_session, "/tmp/policy.pdf", "insurance", 500,
        "insurance policy coverage premium expiry date renewal claims deductible"
    )

    context = generate_domain_context(db_session, "insurance")
    assert context["domain"] == "insurance"
    assert context["document_count"] == 1
    assert len(context["documents"]) == 1
    assert len(context["key_terms"]) > 0
    assert len(context["suggested_templates"]) > 0
    assert "prompt" in context


def test_extract_key_terms():
    """Extract terms from sample text, verify stopwords removed."""
    terms = extract_key_terms(
        "insurance policy coverage premium expiry date renewal insurance policy coverage"
    )
    assert "insurance" in terms
    assert "policy" in terms
    assert "coverage" in terms
    # Stopwords should not appear
    assert "the" not in terms
    assert "and" not in terms


def test_context_summary_empty_db(db_session):
    """Returns valid structure with zero counts when no documents exist."""
    summary = generate_context_summary(db_session)
    assert summary["total_documents"] == 0
    assert summary["total_words"] == 0
    assert summary["domains"] == {}
    assert summary["business_areas"] == []
    assert "No documents" in summary["cta_prompt"]


def test_cta_prompt_generation(db_session):
    """cta_prompt reflects actual domain count."""
    _insert_doc(db_session, "/tmp/a.pdf", "insurance")
    summary = generate_context_summary(db_session)
    assert "1 business area" in summary["cta_prompt"]

    _insert_doc(db_session, "/tmp/b.pdf", "legal")
    summary = generate_context_summary(db_session)
    assert "2 business areas" in summary["cta_prompt"]
