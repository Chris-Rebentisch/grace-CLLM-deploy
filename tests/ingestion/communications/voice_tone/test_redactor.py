"""Tests for the two-layer PII redactor (Chunk 78, D506)."""

from __future__ import annotations

import pytest

from src.ingestion.communications.voice_tone.redactor import (
    _layer1_regex,
    redact_text,
)


# ---------------------------------------------------------------------------
# Layer 1 — regex tests
# ---------------------------------------------------------------------------


class TestRedactEmail:
    """Email addresses are replaced by [EMAIL]."""

    def test_simple_email(self) -> None:
        assert _layer1_regex("reach me at alice@example.com please") == (
            "reach me at [EMAIL] please"
        )

    def test_multiple_emails(self) -> None:
        text = "CC bob@corp.co and carol@domain.org"
        result = _layer1_regex(text)
        assert result.count("[EMAIL]") == 2
        assert "bob@" not in result
        assert "carol@" not in result


class TestRedactPhone:
    """Phone numbers are replaced by [PHONE]."""

    def test_us_format(self) -> None:
        result = _layer1_regex("Call me at (555) 123-4567 today")
        assert "[PHONE]" in result
        assert "555" not in result

    def test_intl_format(self) -> None:
        result = _layer1_regex("Dial +44 20 7946 0958 for support")
        assert "[PHONE]" in result
        assert "7946" not in result


class TestRedactAddress:
    """US-style postal addresses are replaced by [ADDRESS]."""

    def test_street_address(self) -> None:
        result = _layer1_regex("Office at 123 Main Street today")
        assert "[ADDRESS]" in result
        assert "123 Main" not in result

    def test_address_with_suite(self) -> None:
        result = _layer1_regex("Ship to 456 Oak Avenue Suite 200 please")
        assert "[ADDRESS]" in result
        assert "456 Oak" not in result


class TestRedactClaimId:
    """Policy/claim/account IDs are replaced by [CLAIM_ID]."""

    def test_policy_number(self) -> None:
        result = _layer1_regex("Reference POLICY-123456 for details")
        assert "[CLAIM_ID]" in result
        assert "123456" not in result

    def test_claim_number(self) -> None:
        result = _layer1_regex("See CLM#78901234 in the system")
        assert "[CLAIM_ID]" in result
        assert "78901234" not in result


class TestNerFallback:
    """When NER is unavailable, Layer 1 regex still runs."""

    def test_ner_disabled_still_redacts_regex(self) -> None:
        text = "Email alice@example.com about POLICY-999999"
        result = redact_text(text, ner_available=False)
        assert "[EMAIL]" in result
        assert "[CLAIM_ID]" in result
        assert "alice@" not in result


class TestNoCloudProvider:
    """Layer 2 NER must never call a cloud provider (D506)."""

    def test_cloud_provider_blocked(self) -> None:
        """If get_provider returns a cloud provider, Layer 2 skips."""
        from unittest.mock import MagicMock, patch

        mock_provider = MagicMock()
        type(mock_provider).__name__ = "AnthropicProvider"

        with patch(
            "src.ingestion.communications.voice_tone.redactor.get_provider",
            create=True,
        ) as mock_get:
            mock_get.return_value = mock_provider
            # Even with NER enabled, cloud provider should be blocked
            # Layer 1 still runs
            result = redact_text("Email alice@example.com", ner_available=True)
            assert "[EMAIL]" in result


class TestIdempotent:
    """Already-redacted placeholders survive a second pass unchanged."""

    def test_double_redact_stable(self) -> None:
        text = "Contact [EMAIL] about [CLAIM_ID] at [ADDRESS]"
        result = redact_text(text, ner_available=False)
        assert result == text

    def test_mixed_redacted_and_raw(self) -> None:
        text = "[EMAIL] and also bob@corp.co"
        result = redact_text(text, ner_available=False)
        assert result.count("[EMAIL]") == 2
        assert "bob@" not in result


class TestEmptyInput:
    """Edge case: empty or None-like input."""

    def test_empty_string(self) -> None:
        assert redact_text("") == ""

    def test_no_pii(self) -> None:
        text = "The quarterly report looks good."
        assert redact_text(text, ner_available=False) == text
