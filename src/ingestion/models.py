"""Pydantic v2 domain models, ORM models, and enums for Communication Ingestion.

Chunk 55 (D419/D420/D427). All Pydantic fields carry ``description=`` per
CLAUDE.md convention. ORM models map the ``ingestion_sources`` and
``ingestion_runs`` tables created by migration ``c55a_ingest_sources_runs``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from src.shared.database import Base


# ---------------------------------------------------------------------------
# Credentials redaction helper
# ---------------------------------------------------------------------------

_REDACT_KEYS = frozenset(
    {"refresh_token", "access_token", "client_secret", "app_password", "password"}
)


def _redact_credentials(config_json: dict) -> dict:
    """Return a shallow copy of *config_json* with sensitive values replaced by ``'***'``."""
    return {
        k: "***" if k in _REDACT_KEYS else v
        for k, v in config_json.items()
    }


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class IngestionRunStatus(str, enum.Enum):
    """Status of an ingestion run lifecycle.

    Widened from 4 → 5 values in Chunk 57 (``paused`` added for SIGTERM
    graceful-shutdown and APScheduler scheduling).
    """

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    paused = "paused"


class IngestionSourceStatus(str, enum.Enum):
    """Persistent source-level lifecycle status (Chunk 57, D424/D425).

    ``pending`` → ``ready`` (OAuth callback or credential resolution).
    ``ready`` → ``error`` (adapter auth failure).
    ``error`` → ``ready`` (re-auth).
    ``disabled`` — operator-initiated.
    """

    pending = "pending"
    ready = "ready"
    error = "error"
    disabled = "disabled"


# ---------------------------------------------------------------------------
# Pydantic domain models
# ---------------------------------------------------------------------------


class Recipient(BaseModel):
    """An email recipient with role and optional display name."""

    email: EmailStr = Field(description="RFC 5322 email address of the recipient.")
    display_name: str | None = Field(
        default=None,
        description="RFC 5322 phrase part (e.g. 'Alice Example'); None when header has only the address.",
    )
    role: Literal["to", "cc", "bcc"] = Field(
        description="Recipient role in the email header."
    )


class AttachmentRef(BaseModel):
    """Reference to an email attachment — content extraction deferred to Chunk 56."""

    filename: str = Field(description="Original filename of the attachment.")
    mime_type: str = Field(description="MIME type of the attachment.")
    size_bytes: int = Field(description="Size of the attachment in bytes.")
    docling_document_id: UUID | None = Field(
        default=None,
        description="Docling document ID for content extraction. Always None in Chunk 55.",
    )


class CommunicationEvent(BaseModel):
    """A single communication event (email message) ingested into GrACE.

    Twenty fields per spec §6 Step 1. Core fields always populated;
    deferred fields at defaults.
    """

    event_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this communication event.",
    )
    source_id: UUID = Field(description="ID of the ingestion source that produced this event.")
    message_id: str = Field(description="RFC 5322 Message-ID header value.")
    subject: str | None = Field(default=None, description="Email subject line.")
    sender_email: EmailStr = Field(description="Sender's email address.")
    sender_display_name: str | None = Field(
        default=None,
        description="Sender's display name from RFC 5322 From header.",
    )
    recipients: list[Recipient] = Field(
        default_factory=list,
        description="List of recipients with roles.",
    )
    sent_at: datetime | None = Field(
        default=None, description="Date header parsed to UTC."
    )
    received_at: datetime | None = Field(
        default=None, description="Received header or ingestion timestamp."
    )
    body_plain: str | None = Field(default=None, description="Plain-text body.")
    body_html: str | None = Field(default=None, description="HTML body.")
    attachments: list[AttachmentRef] = Field(
        default_factory=list,
        description="Attachment references; content extraction deferred.",
    )
    in_reply_to: str | None = Field(
        default=None, description="In-Reply-To header value."
    )
    references: list[str] = Field(
        default_factory=list,
        description="References header values for threading.",
    )
    thread_id: str | None = Field(
        default=None,
        description="Thread identifier. Deferred — always None in Chunk 55.",
    )
    triage_tier_outcome: str = Field(
        default="pending",
        description="Triage tier outcome. Always 'pending' in Chunk 55.",
    )
    sensitivity_tags: list[str] | None = Field(
        default=None,
        description="Sensitivity tags. Deferred — always None in Chunk 55.",
    )
    ontology_module: str | None = Field(
        default=None, description="Ontology module / segment this event belongs to."
    )
    source_type: str = Field(description="Source type that produced this event (e.g. 'mbox').")
    raw_headers: dict | None = Field(
        default=None,
        description="Preserved raw email headers as key-value dict.",
    )


# ---------------------------------------------------------------------------
# SourceConfig discriminated union (7 variants)
# ---------------------------------------------------------------------------


class MboxSourceConfig(BaseModel):
    """Configuration for mbox file-based ingestion."""

    source_type: Literal["mbox"] = Field(
        default="mbox", description="Discriminator for mbox sources."
    )
    file_path: str = Field(description="Path to the .mbox file.")


class EmlSourceConfig(BaseModel):
    """Configuration for eml file-based ingestion."""

    source_type: Literal["eml"] = Field(
        default="eml", description="Discriminator for eml sources."
    )
    directory_path: str = Field(description="Path to directory containing .eml files.")


class MsgSourceConfig(BaseModel):
    """Configuration for Outlook .msg file-based ingestion."""

    source_type: Literal["msg"] = Field(
        default="msg", description="Discriminator for msg sources."
    )
    directory_path: str = Field(description="Path to directory containing .msg files.")


class PstSourceConfig(BaseModel):
    """Configuration for PST archive ingestion via readpst subprocess."""

    source_type: Literal["pst"] = Field(
        default="pst", description="Discriminator for pst sources."
    )
    file_path: str = Field(description="Path to the .pst file.")
    converted_output_dir: str = Field(
        default="data/ingestion/converted/",
        description="Base directory for readpst output. PstPreconverter appends <source_id>/ at runtime.",
    )


class ImapSourceConfig(BaseModel):
    """IMAP source configuration (Chunk 57 live adapter)."""

    model_config = {"extra": "ignore"}

    source_type: Literal["imap"] = Field(
        default="imap", description="Discriminator for IMAP sources."
    )
    host: str = Field(description="IMAP server hostname.")
    port: int = Field(default=993, description="IMAP server port.")
    username: str = Field(description="IMAP username.")
    password: str = Field(default="", description="IMAP password (on-prem fallback).")
    app_password_env: str | None = Field(
        default=None, description="Env var name holding cloud IMAP app password."
    )
    use_ssl: bool = Field(default=True, description="Whether to use SSL/TLS.")
    schedule_mode: str | None = Field(default=None, description="Schedule mode: interval | one_time.")
    schedule_interval_hours: float | None = Field(default=None, description="Interval in hours between scheduled runs.")
    schedule_enabled: bool = Field(default=False, description="Whether scheduled ingestion is enabled.")


class ExchangeSourceConfig(BaseModel):
    """Exchange/O365 source configuration (Chunk 57 live adapter, OAuth2 via Graph API)."""

    model_config = {"extra": "ignore"}

    source_type: Literal["exchange"] = Field(
        default="exchange", description="Discriminator for Exchange sources."
    )
    server_url: str = Field(default="https://graph.microsoft.com/v1.0", description="Microsoft Graph API base URL.")
    username: str = Field(default="", description="Exchange username (display only).")
    tenant_id: str = Field(default="", description="Azure AD tenant ID.")
    refresh_token_env: str = Field(default="", description="Env var name holding OAuth2 refresh token.")
    schedule_mode: str | None = Field(default=None, description="Schedule mode: interval | one_time.")
    schedule_interval_hours: float | None = Field(default=None, description="Interval in hours between scheduled runs.")
    schedule_enabled: bool = Field(default=False, description="Whether scheduled ingestion is enabled.")


class GmailSourceConfig(BaseModel):
    """Gmail source configuration (Chunk 57 live adapter, OAuth2 via Gmail API)."""

    model_config = {"extra": "ignore"}

    source_type: Literal["gmail"] = Field(
        default="gmail", description="Discriminator for Gmail sources."
    )
    refresh_token_env: str = Field(default="", description="Env var name holding OAuth2 refresh token.")
    schedule_mode: str | None = Field(default=None, description="Schedule mode: interval | one_time.")
    schedule_interval_hours: float | None = Field(default=None, description="Interval in hours between scheduled runs.")
    schedule_enabled: bool = Field(default=False, description="Whether scheduled ingestion is enabled.")


SourceConfig = Annotated[
    MboxSourceConfig
    | EmlSourceConfig
    | MsgSourceConfig
    | PstSourceConfig
    | ImapSourceConfig
    | ExchangeSourceConfig
    | GmailSourceConfig,
    Field(discriminator="source_type"),
]
"""Discriminated union of all source configuration types."""


# ---------------------------------------------------------------------------
# Checkpoint model
# ---------------------------------------------------------------------------


class IngestionCheckpoint(BaseModel):
    """Adapter-specific checkpoint for resumable ingestion."""

    checkpoint_type: Literal["file_offset", "uid_validity", "delta_link", "history_id"] = Field(
        description="Type of checkpoint (adapter-specific)."
    )
    value: str = Field(description="Opaque checkpoint value.")


# ---------------------------------------------------------------------------
# Readiness models
# ---------------------------------------------------------------------------


class ReadinessThresholds(BaseModel):
    """Operator-configurable readiness thresholds for the ingestion gate."""

    cq_mention_threshold: int = Field(
        default=3,
        description="Minimum number of ACCEPTED CQs per segment for readiness.",
    )
    confidence_threshold: float = Field(
        default=0.85,
        description="Minimum extraction_confidence for entity count queries.",
    )


class SegmentReadiness(BaseModel):
    """Per-segment readiness status for the ingestion gate."""

    segment: str = Field(description="Ontology module / segment name.")
    ready: bool = Field(description="Whether this segment meets readiness thresholds.")
    person_count: int = Field(description="Count of Person entities above confidence threshold.")
    organization_count: int = Field(
        description="Count of Organization entities above confidence threshold."
    )
    accepted_cq_count: int = Field(
        description="Count of ACCEPTED competency questions for this segment/domain."
    )
    guidance: str = Field(
        default="",
        description="Human-readable guidance on what's needed to reach readiness.",
    )


class ReadinessResult(BaseModel):
    """Aggregated readiness result from the D274 hybrid Postgres+ArcadeDB gate."""

    deployment_path: Literal["A", "B", "C"] = Field(
        description="Selected deployment path."
    )
    segments: list[SegmentReadiness] = Field(
        description="Per-segment readiness breakdown."
    )
    overall_ready: bool = Field(description="True when all segments are ready.")
    bootstrap_pending: bool = Field(
        default=False,
        description="True when Path B bootstrap is not yet complete.",
    )
    bootstrap_complete: bool = Field(
        default=True,
        description="True when bootstrap curated subset is ready (Path A/C: always True; Path B: True iff curated_email_subsets row with sentinel_status='ready' exists).",
    )
    thresholds: ReadinessThresholds = Field(
        description="Thresholds used for this readiness check."
    )


# ---------------------------------------------------------------------------
# API read shapes
# ---------------------------------------------------------------------------


class SampleDateRange(BaseModel):
    """Date range of sample messages found during a test-connection probe."""

    oldest: datetime = Field(description="Oldest message date in the sample.")
    newest: datetime = Field(description="Newest message date in the sample.")


class ConnectionTestResult(BaseModel):
    """Result of a source test-connection probe (AC-25)."""

    ok: bool = Field(description="Whether the connection test succeeded.")
    sample_message_count: int | None = Field(
        default=None, description="Number of sample messages found."
    )
    sample_date_range: SampleDateRange | None = Field(
        default=None,
        description="Date range of sample messages (ISO-8601). None on failure.",
    )
    error: str | None = Field(
        default=None, description="Error message on failure; None on success."
    )


class IngestionSourceRead(BaseModel):
    """API read shape for ingestion sources — excludes deleted_at, redacts credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Source ID.")
    name: str = Field(description="Source display name.")
    source_type: str = Field(description="Source type discriminator.")
    config_json: dict = Field(description="Source configuration (credentials redacted).")
    segment: str = Field(description="Ontology module / segment.")
    enabled: bool = Field(description="Whether this source is enabled.")
    created_at: datetime = Field(description="Creation timestamp.")
    status: str = Field(default="pending", description="Source lifecycle status (Chunk 57).")

    @classmethod
    def from_orm_row(cls, row: IngestionSource) -> IngestionSourceRead:
        """Build from ORM row with credential redaction."""
        return cls(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            config_json=_redact_credentials(row.config_json or {}),
            segment=row.segment,
            enabled=row.enabled,
            created_at=row.created_at,
            status=getattr(row, "status", "pending"),
        )


class IngestionRunRead(BaseModel):
    """API read shape for ingestion runs — excludes checkpoint_json."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Run ID.")
    source_id: UUID = Field(description="Associated source ID.")
    started_at: datetime = Field(description="Run start timestamp.")
    completed_at: datetime | None = Field(
        default=None, description="Run completion timestamp."
    )
    status: str = Field(description="Run status (pending/running/completed/failed).")
    error_text: str | None = Field(default=None, description="Error text on failure.")
    triage_tier_counts_json: dict | None = Field(
        default=None, description="Triage tier count summary."
    )


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------


class IngestionCheckpointRow(Base):
    """ORM model for ``ingestion_checkpoints`` table (mutable — Chunk 57, D424/D425).

    Single-row-per-source state table for incremental sync cursors.
    Invariant departed: Chunk 36+ append-only pattern.
    Carve-out: mutable state table for incremental sync cursors.
    Authorization: D424/D425.
    """

    __tablename__ = "ingestion_checkpoints"

    source_id = Column(PG_UUID(as_uuid=True), primary_key=True)
    checkpoint_type = Column(Text, nullable=False)
    checkpoint_value = Column(Text, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IngestionSource(Base):
    """ORM model for the ``ingestion_sources`` table (mutable)."""

    __tablename__ = "ingestion_sources"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text, nullable=False, unique=True)
    source_type = Column(Text, nullable=False)
    config_json = Column(JSONB, nullable=False)
    segment = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Text, nullable=False, server_default="pending")


class IngestionRun(Base):
    """ORM model for the ``ingestion_runs`` table (append-only with mutable lifecycle carve-out)."""

    __tablename__ = "ingestion_runs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(PG_UUID(as_uuid=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Text, nullable=False, server_default="pending")
    checkpoint_json = Column(JSONB, nullable=True)
    error_text = Column(Text, nullable=True)
    triage_tier_counts_json = Column(JSONB, nullable=True)


class CommunicationEventRow(Base):
    """ORM model for ``communication_events`` table (append-only with mutable carve-out — Chunk 56, c56a, D435)."""

    __tablename__ = "communication_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id = Column(Text, nullable=False)
    sender_email = Column(Text, nullable=False)
    sender_display_name = Column(Text, nullable=True)
    recipients_json = Column(JSONB, nullable=False)
    subject = Column(Text, nullable=True)
    body_plain = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    ingested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source_id = Column(PG_UUID(as_uuid=True), nullable=False)
    ontology_module = Column(Text, nullable=True)
    attachments_json = Column(JSONB, nullable=True)
    in_reply_to = Column(Text, nullable=True)
    references_json = Column(JSONB, nullable=True)
    thread_id = Column(Text, nullable=True)
    thread_orphan = Column(Boolean, nullable=False, server_default="false")
    thread_position = Column(Integer, nullable=True)
    raw_headers_json = Column(JSONB, nullable=True)
    triage_tier_outcome = Column(Text, nullable=False, server_default="pending")
    sensitivity_tags = Column(Text, nullable=True)
    observed_in_sources_json = Column(JSONB, nullable=True)


class CuratedEmailSubsetRow(Base):
    """ORM model for ``curated_email_subsets`` table (append-only with sentinel_status carve-out — Chunk 56, c56a, D435)."""

    __tablename__ = "curated_email_subsets"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(PG_UUID(as_uuid=True), nullable=False)
    deployment_path = Column(Text, nullable=False)
    selected_message_ids = Column(JSONB, nullable=False)
    diversity_metrics = Column(JSONB, nullable=False)
    created_by = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sentinel_status = Column(Text, nullable=False, server_default="pending")


class CommunicationEventListItem(BaseModel):
    """API list shape for communication events — metadata only, no body/headers/attachments (D435 §40.10)."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID = Field(description="Communication event ID (communication_events.id).")
    message_id: str = Field(description="RFC 5322 Message-ID.")
    sender_email: str = Field(description="Sender email address.")
    sender_display_name: str | None = Field(default=None, description="Sender display name.")
    subject: str | None = Field(default=None, description="Email subject line.")
    sent_at: datetime | None = Field(default=None, description="Sent timestamp.")
    received_at: datetime | None = Field(default=None, description="Received timestamp.")
    triage_tier_outcome: str = Field(description="Triage outcome label.")
