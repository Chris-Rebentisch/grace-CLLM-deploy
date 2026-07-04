"""D298 — Chunk 38 Change_Directives telemetry payload validation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.elicitation.models import (
    ElicitationEventEnvelope,
    validate_payload_for_event_type,
)


def _envelope(event_type: str, payload: dict) -> dict:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "session_id": str(uuid4()),
        "actor_type": "system",
        "phase_name": "structure",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "grace_version": "0.38.0",
        "payload": payload,
        "payload_schema_version": 1,
    }


def test_change_directive_created_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "tier": "Operational_Adjustment",
        "visibility": "permission_matrix_default",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    env = ElicitationEventEnvelope.model_validate(
        _envelope("change_directive_created", payload)
    )
    assert env.event_type == "change_directive_created"
    validate_payload_for_event_type("change_directive_created", payload)


def test_change_directive_created_rejects_unknown_tier():
    payload = {
        "directive_id": str(uuid4()),
        "tier": "Bogus_Tier",
        "visibility": "permission_matrix_default",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with pytest.raises(ValidationError):
        validate_payload_for_event_type("change_directive_created", payload)


def test_change_directive_transitioned_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "from_state": "DRAFT",
        "to_state": "ACTIVE",
        "transitioned_at": datetime.now(timezone.utc).isoformat(),
    }
    env = ElicitationEventEnvelope.model_validate(
        _envelope("change_directive_transitioned", payload)
    )
    assert env.event_type == "change_directive_transitioned"
    validate_payload_for_event_type("change_directive_transitioned", payload)


def test_change_directive_flagged_from_review_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "flagged_from_session_id": str(uuid4()),
        "flagged_from_element_name": "Legal_Entity",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    validate_payload_for_event_type(
        "change_directive_flagged_from_review", payload
    )


def test_evidence_criterion_added_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "criterion_id": str(uuid4()),
        "compilation_status": "approved",
        "has_compiled_query": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    validate_payload_for_event_type(
        "change_directive_evidence_criterion_added", payload
    )


def test_evidence_criterion_added_rejects_unknown_status():
    payload = {
        "directive_id": str(uuid4()),
        "criterion_id": str(uuid4()),
        "compilation_status": "compiled_with_warnings",  # not in literal
        "has_compiled_query": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "change_directive_evidence_criterion_added", payload
        )


def test_change_directive_payloads_reject_extra_fields():
    payload = {
        "directive_id": str(uuid4()),
        "tier": "Operational_Adjustment",
        "visibility": "permission_matrix_default",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "unexpected_field": "noise",
    }
    with pytest.raises(ValidationError):
        validate_payload_for_event_type("change_directive_created", payload)


def test_change_directive_metadata_edited_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "editor_user_id": str(uuid4()),
        "fields_changed": ["title"],
        "before_values": {"title": "a"},
        "after_values": {"title": "b"},
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }
    ElicitationEventEnvelope.model_validate(
        _envelope("change_directive_metadata_edited", payload)
    )
    validate_payload_for_event_type(
        "change_directive_metadata_edited", payload
    )


def test_change_directive_detail_viewed_payload_validates():
    payload = {
        "directive_id": str(uuid4()),
        "tier": "Operational_Adjustment",
        "viewer_user_id": str(uuid4()),
        "viewed_at": datetime.now(timezone.utc).isoformat(),
    }
    ElicitationEventEnvelope.model_validate(
        _envelope("change_directive_detail_viewed", payload)
    )
    validate_payload_for_event_type(
        "change_directive_detail_viewed", payload
    )
