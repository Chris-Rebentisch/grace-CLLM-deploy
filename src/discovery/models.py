"""Discovery module data models: ProcessedDocument, enums, and config loading."""

from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from pydantic import BaseModel, Field, model_validator


class FileType(str, Enum):
    """Supported document types that Docling can process."""

    PDF = "PDF"
    DOCX = "DOCX"
    XLSX = "XLSX"
    PPTX = "PPTX"
    HTML = "HTML"
    TXT = "TXT"
    CSV = "CSV"
    EMAIL = "EMAIL"
    IMAGE = "IMAGE"
    OTHER = "OTHER"


class ProcessingStatus(str, Enum):
    """Tracks where each document is in the Discovery pipeline."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# --- Discovery YAML config loading ---

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"


@lru_cache
def load_discovery_config() -> dict:
    """Load and cache the Discovery YAML configuration."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_valid_domains() -> list[str]:
    """Return the list of valid domain categories from discovery.yaml."""
    config = load_discovery_config()
    return config["domain_categories"]


# --- Pydantic model ---


class ProcessedDocument(BaseModel):
    """A document that has been processed by the Discovery document pipeline."""

    id: UUID = Field(default_factory=uuid4, description="Unique document identifier")
    file_path: str = Field(description="Original source path of the document")
    file_name: str = Field(description="File name without directory path")
    file_type: FileType = Field(description="Document format type")
    file_size_bytes: int = Field(description="File size in bytes")
    created_at: datetime | None = Field(
        default=None, description="File creation timestamp"
    )
    modified_at: datetime | None = Field(
        default=None, description="File last modified timestamp"
    )
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When GrACE processed this document",
    )
    project: str = Field(
        default="",
        description="Project association (from directory structure or manual mapping)",
    )
    domain: str = Field(
        default="other",
        description="Domain category (from config/discovery.yaml)",
    )
    extracted_text: str = Field(
        default="", description="Clean text output — Markdown export from Docling"
    )
    docling_document_json: dict | None = Field(
        default=None, description="Full lossless DoclingDocument as JSON dict"
    )
    word_count: int = Field(default=0, description="Word count of extracted text")
    status: ProcessingStatus = Field(
        default=ProcessingStatus.QUEUED, description="Processing pipeline status"
    )
    error_message: str | None = Field(
        default=None, description="Error details if processing failed"
    )
    metadata_extra: dict = Field(
        default_factory=dict, description="Additional metadata (JSONB)"
    )
    # D518 — email-origin row discrimination columns.
    origin: str | None = Field(
        default=None,
        description="Row provenance — NULL for document-derived, 'curated_email' for bootstrap pipe",
    )
    source_type: str | None = Field(
        default=None,
        description="Ingestion source type identifier",
    )

    @model_validator(mode="after")
    def validate_domain(self) -> "ProcessedDocument":
        """Validate that domain is in the configured domain categories."""
        valid = get_valid_domains()
        if self.domain not in valid:
            raise ValueError(
                f"Invalid domain '{self.domain}'. Must be one of: {valid}"
            )
        return self
