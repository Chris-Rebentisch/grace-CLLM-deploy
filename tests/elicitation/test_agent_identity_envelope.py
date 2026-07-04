"""CP3 — ElicitationEventEnvelope agent identity extension tests (D364).

Covers:
- Backward-compatible envelope validation (legacy agent_id=None).
- New shape (agent_id, delegation_source) accepted.
- ActorType admits "agent".
- Existing event validation unchanged.
- Seven new EventType entries accepted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.elicitation.models import (
    ActorType,
    ElicitationEventEnvelope,
    EventType,
    validate_payload_for_event_type,
)


def _base_envelope(**overrides) -> dict:
    """Build a minimal valid envelope dict."""
    base = {
        "event_id": str(uuid4()),
        "event_type": "session_started",
        "session_id": str(uuid4()),
        "actor_type": "human",
        "phase_name": "prepare",
        "emitted_at": datetime.now().isoformat(),
        "schema_version": 1,
        "grace_version": "0.44.0",
        "payload": {"plan_id": None, "instrument_selected": None, "rationale_string": None},
        "payload_schema_version": 1,
    }
    base.update(overrides)
    return base


def test_legacy_envelope_no_agent_fields():
    """Legacy envelope without agent fields is accepted (backward compat)."""
    env = ElicitationEventEnvelope(**_base_envelope())
    assert env.agent_id is None
    assert env.agent_display_name is None
    assert env.delegation_source is None


def test_envelope_with_agent_fields():
    """Envelope with all three agent fields accepted."""
    env = ElicitationEventEnvelope(
        **_base_envelope(
            agent_id="cowork-1",
            agent_display_name="Cowork Plugin",
            delegation_source="agent_on_behalf",
            actor_type="agent",
        )
    )
    assert env.agent_id == "cowork-1"
    assert env.agent_display_name == "Cowork Plugin"
    assert env.delegation_source == "agent_on_behalf"


def test_envelope_delegation_source_user_direct():
    """delegation_source='user_direct' accepted."""
    env = ElicitationEventEnvelope(
        **_base_envelope(delegation_source="user_direct")
    )
    assert env.delegation_source == "user_direct"


def test_envelope_delegation_source_system_scheduled():
    """delegation_source='system_scheduled' accepted."""
    env = ElicitationEventEnvelope(
        **_base_envelope(delegation_source="system_scheduled")
    )
    assert env.delegation_source == "system_scheduled"


def test_envelope_invalid_delegation_source():
    """Invalid delegation_source rejected by Pydantic."""
    with pytest.raises(ValidationError):
        ElicitationEventEnvelope(
            **_base_envelope(delegation_source="invalid_value")
        )


def test_actor_type_agent():
    """ActorType admits 'agent' as third value."""
    env = ElicitationEventEnvelope(
        **_base_envelope(actor_type="agent")
    )
    assert env.actor_type == "agent"


def test_new_event_types_accepted():
    """Seven new MCP EventType entries are valid."""
    new_types = [
        "mcp_session_started",
        "mcp_session_phase_advanced",
        "mcp_session_closed",
        "mcp_review_decided",
        "mcp_laddering_followup_emitted",
        "mcp_teachback_captured",
        "mcp_deep_link_generated",
    ]
    for event_type in new_types:
        env = ElicitationEventEnvelope(
            **_base_envelope(event_type=event_type, payload={
                "session_id": str(uuid4()),
            })
        )
        assert env.event_type == event_type


def test_existing_event_type_still_valid():
    """Pre-existing event types unchanged."""
    env = ElicitationEventEnvelope(
        **_base_envelope(event_type="session_started")
    )
    assert env.event_type == "session_started"
