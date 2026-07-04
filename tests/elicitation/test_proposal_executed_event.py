"""Tests for proposal_executed EventType (Chunk 48, CP6)."""

from datetime import UTC, datetime
from uuid import uuid4

from src.elicitation.models import (
    ElicitationEventEnvelope,
    ProposalExecutedPayload,
    validate_payload_for_event_type,
)


class TestProposalExecutedPayload:
    def test_payload_shape(self) -> None:
        """proposal_executed payload validates with correct fields."""
        payload = ProposalExecutedPayload(
            proposal_id="abc-123",
            tier=2,
            outcome="applied",
        )
        assert payload.proposal_id == "abc-123"
        assert payload.tier == 2
        assert payload.outcome == "applied"

    def test_round_trip_through_envelope(self) -> None:
        """proposal_executed validates through validate_payload_for_event_type + envelope."""
        raw = {"proposal_id": str(uuid4()), "tier": 1, "outcome": "gate_failed"}
        validated = validate_payload_for_event_type("proposal_executed", raw)
        assert isinstance(validated, ProposalExecutedPayload)

        envelope = ElicitationEventEnvelope(
            event_id=uuid4(),
            event_type="proposal_executed",
            session_id=uuid4(),
            actor_type="system",
            phase_name="none",
            emitted_at=datetime.now(UTC),
            schema_version=1,
            grace_version="0.1.0",
            payload=validated.model_dump(mode="json"),
            payload_schema_version=1,
        )
        assert envelope.event_type == "proposal_executed"
