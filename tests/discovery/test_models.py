"""Tests for Discovery module models and config."""

import pytest
from pydantic import ValidationError

from src.discovery.models import (
    FileType,
    ProcessedDocument,
    ProcessingStatus,
    get_valid_domains,
    load_discovery_config,
)


def test_processed_document_defaults():
    """Creating a ProcessedDocument with only required fields produces valid defaults."""
    doc = ProcessedDocument(
        file_path="/tmp/test.pdf",
        file_name="test.pdf",
        file_type=FileType.PDF,
        file_size_bytes=1024,
    )
    assert doc.status == ProcessingStatus.QUEUED
    assert doc.domain == "other"
    assert doc.project == ""
    assert doc.word_count == 0
    assert doc.extracted_text == ""
    assert doc.error_message is None
    assert doc.metadata_extra == {}
    assert doc.id is not None
    assert doc.processed_at is not None


def test_file_type_enum():
    """All FileType values are valid strings."""
    expected = {"PDF", "DOCX", "XLSX", "PPTX", "HTML", "TXT", "CSV", "EMAIL", "IMAGE", "OTHER"}
    assert {ft.value for ft in FileType} == expected


def test_processing_status_enum():
    """All ProcessingStatus values are valid strings."""
    expected = {"QUEUED", "PROCESSING", "COMPLETE", "FAILED", "SKIPPED"}
    assert {ps.value for ps in ProcessingStatus} == expected


def test_domain_validation():
    """A domain from discovery.yaml passes; an invalid domain is rejected."""
    # Valid domain
    doc = ProcessedDocument(
        file_path="/tmp/test.pdf",
        file_name="test.pdf",
        file_type=FileType.PDF,
        file_size_bytes=1024,
        domain="legal",
    )
    assert doc.domain == "legal"

    # Invalid domain
    with pytest.raises(ValidationError, match="Invalid domain"):
        ProcessedDocument(
            file_path="/tmp/test2.pdf",
            file_name="test2.pdf",
            file_type=FileType.PDF,
            file_size_bytes=1024,
            domain="nonexistent_domain",
        )


def test_discovery_config_loads():
    """load_discovery_config() returns valid config from YAML."""
    config = load_discovery_config()
    assert "domain_categories" in config
    assert "supported_extensions" in config
    assert isinstance(config["domain_categories"], list)
    assert len(config["domain_categories"]) > 0


def test_valid_domains_from_config():
    """get_valid_domains() returns the expected list."""
    domains = get_valid_domains()
    assert "other" in domains
    assert "legal" in domains
    assert "corporate_structure" in domains
    assert isinstance(domains, list)
