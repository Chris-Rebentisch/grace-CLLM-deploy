"""Tests for D267 ``RetrievalQueryReplayedPayload`` extension (Chunk 35b CP5).

Verifies that the two new optional fields (``replay_differed``,
``original_query_event_id``) coexist with the existing required fields
(``strategies_fired``, ``latency_ms_total``) without breaking backward
compatibility.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.elicitation.models import RetrievalQueryReplayedPayload


def test_payload_with_both_new_fields_validates():
    """All fields populated — happy path."""
    payload = RetrievalQueryReplayedPayload(
        strategies_fired=["semantic", "bm25"],
        latency_ms_total=42.0,
        replay_differed=True,
        original_query_event_id="11111111-1111-1111-1111-111111111111",
    )
    assert payload.strategies_fired == ["semantic", "bm25"]
    assert payload.latency_ms_total == 42.0
    assert payload.replay_differed is True
    assert payload.original_query_event_id == "11111111-1111-1111-1111-111111111111"


def test_payload_defaults_only_remains_backward_compatible():
    """Existing events without the new fields still validate.

    Backward-compat guarantee: the payload accepts the pre-35b shape
    (just ``strategies_fired`` + ``latency_ms_total``) and applies
    defaults for ``replay_differed`` (False) and
    ``original_query_event_id`` (None).
    """
    payload = RetrievalQueryReplayedPayload(
        strategies_fired=["semantic"],
        latency_ms_total=12.5,
    )
    assert payload.replay_differed is False
    assert payload.original_query_event_id is None


def test_payload_rejects_unknown_extra_field():
    """B3 resolution: ``extra='forbid'`` rejects unknown field names.

    This exercises the ConfigDict enforcement (not malformed values in
    known fields). The extra-field key ``rogue_field`` is unknown to the
    model, so Pydantic raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        RetrievalQueryReplayedPayload(
            strategies_fired=["semantic"],
            latency_ms_total=1.0,
            replay_differed=False,
            rogue_field="should-be-rejected",
        )
