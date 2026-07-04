"""Tests for Voice & Tone models (Chunk 58, CP3).

Validates:
1. Pydantic validation for all model classes
2. Mutual-exclusion CHECK mirror
3. Band-only fields (D120/D217)
4. role_to_category_map rejects non-D422 categories
5. organization_domains rejects protocol prefix
6. DpiaAttestationRequest hex validation
7. All ten D422 categories accepted
8. StyleSignature all-band validation
9. VoiceToneConfig defaults
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.ingestion.communications.voice_tone.models import (
    CommunicationStyleProfile,
    D422_CATEGORIES,
    DpiaAttestationRequest,
    FeatureResult,
    RecipientStyleProfile,
    StyleDelta,
    StyleSignature,
    VoiceToneConfig,
)


class TestStyleSignature:
    """StyleSignature model validation."""

    def test_valid_signature(self):
        sig = StyleSignature(
            sentence_length_band="high",
            vocabulary_complexity_band="medium",
            formality_band="low",
            greeting_closing_band="high",
            hedging_frequency_band="medium",
            directness_band="low",
            response_timing_band="high",
            thread_depth_band="medium",
        )
        assert sig.sentence_length_band == "high"

    def test_rejects_invalid_band(self):
        with pytest.raises(Exception):
            StyleSignature(
                sentence_length_band="very_high",  # invalid
                vocabulary_complexity_band="medium",
                formality_band="low",
                greeting_closing_band="high",
                hedging_frequency_band="medium",
                directness_band="low",
                response_timing_band="high",
                thread_depth_band="medium",
            )


class TestCommunicationStyleProfile:
    """CommunicationStyleProfile mutual-exclusion and validation."""

    def _make_sig(self):
        return StyleSignature(
            sentence_length_band="medium",
            vocabulary_complexity_band="medium",
            formality_band="medium",
            greeting_closing_band="medium",
            hedging_frequency_band="medium",
            directness_band="medium",
            response_timing_band="medium",
            thread_depth_band="medium",
        )

    def test_valid_individual_profile(self):
        p = CommunicationStyleProfile(
            sender_person_id=uuid4(),
            profile_version=1,
            style_signature=self._make_sig(),
            profile_quality_band="high",
        )
        assert p.aggregate_segment is None

    def test_valid_aggregate_profile(self):
        p = CommunicationStyleProfile(
            aggregate_segment="engineering",
            profile_version=1,
            style_signature=self._make_sig(),
            profile_quality_band="medium",
        )
        assert p.sender_person_id is None

    def test_mutual_exclusion_both_set(self):
        """Cannot set both sender_person_id and aggregate_segment."""
        with pytest.raises(ValueError, match="Exactly one"):
            CommunicationStyleProfile(
                sender_person_id=uuid4(),
                aggregate_segment="engineering",
                profile_version=1,
                style_signature=self._make_sig(),
                profile_quality_band="medium",
            )

    def test_mutual_exclusion_neither_set(self):
        """Cannot leave both sender_person_id and aggregate_segment unset."""
        with pytest.raises(ValueError, match="Exactly one"):
            CommunicationStyleProfile(
                profile_version=1,
                style_signature=self._make_sig(),
                profile_quality_band="medium",
            )


class TestRecipientStyleProfile:
    """RecipientStyleProfile D422 category validation."""

    def test_all_d422_categories_accepted(self):
        """All ten D422 categories are accepted."""
        for cat in sorted(D422_CATEGORIES):
            p = RecipientStyleProfile(
                profile_id=uuid4(),
                recipient_person_id=uuid4(),
                category=cat,
                confidence_band="high",
                style_delta=StyleDelta(),
            )
            assert p.category == cat

    def test_rejects_non_d422_category(self):
        with pytest.raises(ValueError, match="must be one of"):
            RecipientStyleProfile(
                profile_id=uuid4(),
                recipient_person_id=uuid4(),
                category="unknown_category",
                confidence_band="high",
                style_delta=StyleDelta(),
            )


class TestDpiaAttestationRequest:
    """DpiaAttestationRequest validation."""

    def test_valid_request(self):
        req = DpiaAttestationRequest(
            signed_by="Jane Smith",
            signed_role="DPO",
            signed_at_iso=datetime.now(tz=timezone.utc),
            dpia_template_content_sha256="a" * 64,
        )
        assert req.dpia_template_content_sha256 == "a" * 64

    def test_rejects_non_hex_sha256(self):
        with pytest.raises(ValueError, match="64-char hex"):
            DpiaAttestationRequest(
                signed_by="Jane Smith",
                signed_role="DPO",
                signed_at_iso=datetime.now(tz=timezone.utc),
                dpia_template_content_sha256="not-a-hex-string" + "x" * 48,
            )


class TestVoiceToneConfig:
    """VoiceToneConfig validation."""

    def test_defaults(self):
        config = VoiceToneConfig()
        assert config.organization_domains == []
        assert config.retention_versions == 4
        assert config.profile_minimum_emails_to_generate == 50
        assert len(config.hedging_lexicon) > 0

    def test_rejects_protocol_prefix(self):
        with pytest.raises(ValueError, match="protocol prefix"):
            VoiceToneConfig(organization_domains=["https://example.com"])

    def test_rejects_non_d422_role_map(self):
        with pytest.raises(ValueError, match="D422 category"):
            VoiceToneConfig(role_to_category_map={"ceo": "boss"})
