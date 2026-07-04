"""Contract tests for RecipientClassifier (Chunk 78, CP8).

Validates that the Chunk 58 classifier interface is unchanged
and D422_CATEGORIES remains stable at 10 values.
"""

from __future__ import annotations

import inspect

from src.ingestion.communications.voice_tone.models import D422_CATEGORIES, Band
from src.ingestion.communications.voice_tone.recipient_classifier import (
    RecipientClassifier,
)


class TestRecipientClassifierContract:
    def test_classify_return_type(self) -> None:
        """classify() return annotation is tuple[str, Band]."""
        hints = inspect.get_annotations(RecipientClassifier.classify, eval_str=True)
        # Return type should be tuple[str, Band]
        assert "return" in hints
        ret = hints["return"]
        assert hasattr(ret, "__origin__") and ret.__origin__ is tuple

    def test_d422_categories_count(self) -> None:
        """D422_CATEGORIES contains exactly 10 values."""
        assert len(D422_CATEGORIES) == 10

    def test_classify_signature_unchanged(self) -> None:
        """classify() parameter names match Chunk 58 baseline."""
        sig = inspect.signature(RecipientClassifier.classify)
        param_names = list(sig.parameters.keys())
        expected = [
            "self",
            "recipient_email",
            "canonical_grace_id",
            "config",
            "signature_title",
            "signature_org",
            "thread_depth",
            "response_timing_band",
            "cc_position",
            "representative_bodies",
            "sender_person_id",
            "profile_version",
        ]
        assert param_names == expected
