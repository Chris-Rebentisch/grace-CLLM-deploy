"""Tests for src/extraction/email_composer.py (CP2, D509)."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.extraction.email_composer import (
    CommunicationEventRow,
    compose_extraction_document,
    strip_quoted_history,
)


def _make_event(**kwargs) -> CommunicationEventRow:
    """Helper to build a CommunicationEventRow with defaults."""
    defaults = {
        "message_id": "msg-001@example.com",
        "sender_display_name": "Alice Smith",
        "sender_email": "alice@example.com",
        "subject": "Q1 Report",
        "sent_at": datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        "body_plain": "Hello, this is the body.",
    }
    defaults.update(kwargs)
    return CommunicationEventRow(**defaults)


class TestStripQuotedHistory:
    """Tests for strip_quoted_history()."""

    def test_plain_quote_strip(self):
        """Quoted lines (>) are removed."""
        body = "Thanks for the update.\n\n> On Mon wrote:\n> Previous message\n> Another line"
        result = strip_quoted_history(body)
        assert "Previous message" not in result
        assert "Another line" not in result
        assert "Thanks for the update" in result

    def test_nested_quote_strip(self):
        """Multi-level quoting with 'On ... wrote:' marker is removed."""
        body = "My reply here.\n\nOn 2026-01-01 Bob wrote:\n> First level quote\n>> Second level\n>>> Third level"
        result = strip_quoted_history(body)
        assert "First level quote" not in result
        assert "Second level" not in result
        assert "Third level" not in result
        assert "My reply here" in result

    def test_no_quote_passthrough(self):
        """Text without quotes passes through unchanged."""
        body = "This is a simple email with no quoted content.\nSecond line here."
        result = strip_quoted_history(body)
        assert "This is a simple email" in result
        assert "Second line here" in result

    def test_regex_fallback_on_empty_library_result(self):
        """When parse_reply() returns empty, regex fallback runs."""
        body = "Visible text.\n\nOn 2026-01-01 someone wrote:\n> old content"

        with patch(
            "src.extraction.email_composer.EmailReplyParser"
        ) as mock_parser_cls:
            instance = mock_parser_cls.return_value
            instance.parse_reply.return_value = ""  # Empty result triggers fallback

            result = strip_quoted_history(body)

        # Regex fallback should have stripped quoted content
        assert "Visible text" in result
        assert "old content" not in result


class TestComposeExtractionDocument:
    """Tests for compose_extraction_document()."""

    def test_signature_strip_sender_anchor(self):
        """Signatures are stripped and sender anchor is prepended with correct fields."""
        event = _make_event(
            body_plain="Important content here.\n\n-- \nAlice Smith\nCEO"
        )
        result = compose_extraction_document(event)

        # Signature stripped
        assert "CEO" not in result

        # Sender anchor present
        assert "From: Alice Smith <alice@example.com>" in result
        assert "Date: 2026-03-15T10:00:00+00:00" in result
        assert "Subject: Q1 Report" in result
        assert "---" in result

        # Body content preserved
        assert "Important content here" in result

    def test_missing_fields_omitted_from_anchor(self):
        """Missing optional fields are omitted from the anchor."""
        event = _make_event(
            sender_display_name=None,
            subject=None,
            sent_at=None,
            body_plain="Just the body.",
        )
        result = compose_extraction_document(event)

        # Only email address in From line
        assert "From: alice@example.com" in result
        # No Date or Subject lines
        assert "Date:" not in result
        assert "Subject:" not in result
        # Body present
        assert "Just the body" in result

    def test_empty_body_produces_anchor_only(self):
        """Empty body still produces a valid anchor."""
        event = _make_event(body_plain="")
        result = compose_extraction_document(event)
        assert "From: Alice Smith <alice@example.com>" in result

    def test_sender_identity_sentence_present(self):
        """F-024 / ISS-0016: anchor spells out the full sender identity so a
        first-name sign-off binds to the full canonical name."""
        event = _make_event(
            body_plain="Quick note about the roof bid.\n\nAlice"
        )
        result = compose_extraction_document(event)
        assert "This email was written by Alice Smith" in result
        assert "first-name sign-offs in the body refer to Alice Smith" in result

    def test_no_sender_identity_sentence_without_display_name(self):
        """No display name → no identity sentence (nothing to bind to)."""
        event = _make_event(sender_display_name=None, body_plain="Body.")
        result = compose_extraction_document(event)
        assert "This email was written by" not in result
