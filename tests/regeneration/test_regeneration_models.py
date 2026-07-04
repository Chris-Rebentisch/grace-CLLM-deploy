"""Tests for regeneration_models Pydantic contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.regeneration.regeneration_models import (
    ClaimSpan,
    RegenerationQuery,
    RegenerationResponse,
    ResponseMetadata,
)


def test_phase_state_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        RegenerationQuery(query_text="hi", phase_state="bogus")


def test_claim_span_span_confidence_defaults_low() -> None:
    span = ClaimSpan(
        text="sentence.",
        sentence_indices=[0],
        certainty_band="low",
    )
    assert span.span_confidence == "low"


def test_claim_span_char_offsets_optional() -> None:
    span = ClaimSpan(
        text="sentence.",
        sentence_indices=[0],
        certainty_band="medium",
    )
    assert span.start_char is None
    assert span.end_char is None

    span_with_offsets = ClaimSpan(
        text="sentence.",
        sentence_indices=[0],
        start_char=5,
        end_char=14,
        certainty_band="medium",
    )
    assert span_with_offsets.start_char == 5
    assert span_with_offsets.end_char == 14


def test_response_metadata_defaults() -> None:
    meta = ResponseMetadata(phase_style_applied="directive")
    assert meta.span_detection_note is None
    assert meta.model_override_applied is False
    assert meta.context_truncated is False
    assert meta.span_detector_mode == "sentence_fallback"


def test_regeneration_query_retrieval_query_typed_not_dict() -> None:
    # Valid: retrieval_query as dict that matches RetrievalQuery shape
    query = RegenerationQuery(
        query_text="hi",
        retrieval_query={"query_text": "hi", "top_k": 5},
    )
    assert query.retrieval_query is not None
    assert query.retrieval_query.top_k == 5

    # Invalid: retrieval_query dict missing required or wrong type
    with pytest.raises(ValidationError):
        RegenerationQuery(
            query_text="hi",
            retrieval_query={"top_k": "not-an-int"},
        )


def test_regeneration_response_round_trips() -> None:
    meta = ResponseMetadata(phase_style_applied="d")
    resp = RegenerationResponse(
        query="q",
        response_text="text",
        phase_state="none",
        response_metadata=meta,
    )
    payload = resp.model_dump_json()
    restored = RegenerationResponse.model_validate_json(payload)
    assert restored.query == "q"
    assert restored.response_text == "text"
    assert restored.phase_state == "none"
    assert restored.response_metadata.phase_style_applied == "d"
