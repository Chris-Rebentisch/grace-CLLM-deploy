"""Tests for Chunk 55 ingestion domain models (CP1).

~17 tests covering: SourceConfig discriminated union dispatch for all 7
source_type values + invalid rejection; Recipient EmailStr; AttachmentRef
shape; CommunicationEvent defaults; IngestionCheckpoint variants;
ReadinessThresholds + ReadinessResult; SegmentReadiness; IngestionRunStatus
enum; ORM instantiation; _redact_credentials helper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    EmlSourceConfig,
    ExchangeSourceConfig,
    GmailSourceConfig,
    ImapSourceConfig,
    IngestionCheckpoint,
    IngestionRun,
    IngestionRunRead,
    IngestionRunStatus,
    IngestionSource,
    IngestionSourceRead,
    MboxSourceConfig,
    MsgSourceConfig,
    PstSourceConfig,
    ReadinessResult,
    ReadinessThresholds,
    Recipient,
    SampleDateRange,
    SegmentReadiness,
    SourceConfig,
    ConnectionTestResult,
    _redact_credentials,
)


_SourceConfigAdapter = TypeAdapter(SourceConfig)


# -- SourceConfig discriminated union --

class TestSourceConfigDiscrimination:
    """Discriminated union dispatches correctly for all 7 source_type values."""

    def test_mbox(self):
        cfg = _SourceConfigAdapter.validate_python({"source_type": "mbox", "file_path": "/a.mbox"})
        assert isinstance(cfg, MboxSourceConfig)

    def test_eml(self):
        cfg = _SourceConfigAdapter.validate_python({"source_type": "eml", "directory_path": "/eml"})
        assert isinstance(cfg, EmlSourceConfig)

    def test_msg(self):
        cfg = _SourceConfigAdapter.validate_python({"source_type": "msg", "directory_path": "/msg"})
        assert isinstance(cfg, MsgSourceConfig)

    def test_pst(self):
        cfg = _SourceConfigAdapter.validate_python({"source_type": "pst", "file_path": "/a.pst"})
        assert isinstance(cfg, PstSourceConfig)
        assert cfg.converted_output_dir == "data/ingestion/converted/"

    def test_imap(self):
        cfg = _SourceConfigAdapter.validate_python(
            {"source_type": "imap", "host": "mail.example.com", "username": "u"}
        )
        assert isinstance(cfg, ImapSourceConfig)
        assert cfg.port == 993

    def test_exchange(self):
        cfg = _SourceConfigAdapter.validate_python(
            {"source_type": "exchange", "server_url": "https://ex.example.com/ews", "username": "u"}
        )
        assert isinstance(cfg, ExchangeSourceConfig)

    def test_gmail(self):
        cfg = _SourceConfigAdapter.validate_python({"source_type": "gmail"})
        assert isinstance(cfg, GmailSourceConfig)

    def test_invalid_source_type_rejected(self):
        with pytest.raises(ValidationError):
            _SourceConfigAdapter.validate_python({"source_type": "ftp", "file_path": "/a"})


# -- Recipient --

class TestRecipient:
    def test_valid_recipient(self):
        r = Recipient(email="alice@example.com", role="to")
        assert r.display_name is None

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            Recipient(email="not-an-email", role="to")


# -- AttachmentRef --

def test_attachment_ref_shape():
    a = AttachmentRef(filename="doc.pdf", mime_type="application/pdf", size_bytes=1024)
    assert a.docling_document_id is None


# -- CommunicationEvent --

def test_communication_event_defaults():
    evt = CommunicationEvent(
        source_id=uuid4(),
        message_id="<abc@example.com>",
        sender_email="alice@example.com",
        source_type="mbox",
    )
    assert evt.thread_id is None
    assert evt.triage_tier_outcome == "pending"
    assert evt.sensitivity_tags is None
    assert evt.recipients == []
    assert evt.attachments == []


# -- IngestionCheckpoint --

class TestIngestionCheckpoint:
    def test_file_offset(self):
        cp = IngestionCheckpoint(checkpoint_type="file_offset", value="4096")
        assert cp.checkpoint_type == "file_offset"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            IngestionCheckpoint(checkpoint_type="bad_type", value="x")


# -- ReadinessThresholds + ReadinessResult --

def test_readiness_thresholds_defaults():
    t = ReadinessThresholds()
    assert t.cq_mention_threshold == 3
    assert t.confidence_threshold == 0.85


def test_readiness_result_thresholds_roundtrip():
    t = ReadinessThresholds(cq_mention_threshold=5, confidence_threshold=0.9)
    r = ReadinessResult(
        deployment_path="A",
        segments=[],
        overall_ready=True,
        thresholds=t,
    )
    assert r.thresholds.cq_mention_threshold == 5


# -- SegmentReadiness --

def test_segment_readiness_shape():
    sr = SegmentReadiness(
        segment="insurance",
        ready=True,
        person_count=10,
        organization_count=5,
        accepted_cq_count=7,
    )
    assert sr.accepted_cq_count == 7


# -- IngestionRunStatus --

def test_ingestion_run_status_values():
    values = {s.value for s in IngestionRunStatus}
    # Chunk 57: widened 4→5 with 'paused' for SIGTERM graceful shutdown
    assert values == {"pending", "running", "completed", "failed", "paused"}


# -- ORM instantiation --

def test_orm_ingestion_source():
    src = IngestionSource(
        id=uuid4(),
        name="test-source",
        source_type="mbox",
        config_json={"file_path": "/a.mbox"},
        segment="insurance",
    )
    assert src.source_type == "mbox"


# -- _redact_credentials --

def test_redact_credentials():
    data = {
        "host": "mail.example.com",
        "password": "secret123",
        "refresh_token": "tok",
        "access_token": "acc",
        "client_secret": "cs",
        "app_password": "ap",
        "username": "alice",
    }
    redacted = _redact_credentials(data)
    assert redacted["host"] == "mail.example.com"
    assert redacted["username"] == "alice"
    for key in ("password", "refresh_token", "access_token", "client_secret", "app_password"):
        assert redacted[key] == "***"
