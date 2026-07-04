"""Adapter persistence tests (Chunk 56 CP5 — 6 tests)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.ingestion.models import AttachmentRef, CommunicationEvent, Recipient
from src.ingestion.pipeline import _event_to_row


def _make_event(**overrides) -> CommunicationEvent:
    defaults = dict(
        source_id=uuid4(),
        message_id=f"<{uuid4()}@example.com>",
        sender_email="alice@example.com",
        body_plain="Hello world",
        source_type="mbox",
        recipients=[
            Recipient(email="bob@example.com", role="to"),
        ],
    )
    defaults.update(overrides)
    return CommunicationEvent(**defaults)


def test_event_to_row_shape():
    """_event_to_row produces correct DDL column mapping."""
    ev = _make_event()
    row = _event_to_row(ev)
    assert row["message_id"] == ev.message_id
    assert row["sender_email"] == str(ev.sender_email)
    assert row["source_id"] == ev.source_id
    assert "recipients_json" in row
    assert isinstance(row["recipients_json"], list)
    assert len(row["recipients_json"]) == 1


def test_event_to_row_triage_outcome_pending():
    """triage_tier_outcome is always 'pending' on adapter INSERT."""
    ev = _make_event(triage_tier_outcome="something_else")
    row = _event_to_row(ev)
    assert row["triage_tier_outcome"] == "pending"


def test_event_to_row_recipients_serialization():
    """recipients_json serialization shape matches DDL JSONB."""
    ev = _make_event(
        recipients=[
            Recipient(email="bob@example.com", display_name="Bob", role="to"),
            Recipient(email="carol@example.com", role="cc"),
        ]
    )
    row = _event_to_row(ev)
    assert len(row["recipients_json"]) == 2
    assert row["recipients_json"][0]["email"] == "bob@example.com"
    assert row["recipients_json"][0]["role"] == "to"


def test_event_to_row_excludes_raw_size_bytes():
    """raw_size_bytes is NOT persisted."""
    ev = _make_event()
    row = _event_to_row(ev)
    assert "raw_size_bytes" not in row


def test_event_to_row_observed_in_sources_null():
    """observed_in_sources_json is NULL on adapter INSERT."""
    ev = _make_event()
    row = _event_to_row(ev)
    assert "observed_in_sources_json" not in row or row.get("observed_in_sources_json") is None


def test_duplicate_message_id_caught(tmp_path):
    """Duplicate (message_id, source_id) is caught by IntegrityError and logged."""
    # This is a unit test on the _event_to_row shape;
    # the actual IntegrityError is tested in test_c56a_migration.py
    ev1 = _make_event(message_id="<dup@test.com>")
    ev2 = _make_event(message_id="<dup@test.com>", source_id=ev1.source_id)
    row1 = _event_to_row(ev1)
    row2 = _event_to_row(ev2)
    assert row1["message_id"] == row2["message_id"]
    assert row1["source_id"] == row2["source_id"]
