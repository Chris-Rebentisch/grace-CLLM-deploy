"""Tests for Chunk 47 proposal telemetry payloads (D387/D389, CF1 lockstep)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.elicitation.models import (
    ProposalDecidedPayload,
    ProposalGeneratedPayload,
    ProposalViewedPayload,
    payload_model_for,
    validate_payload_for_event_type,
)


class TestProposalGeneratedPayload:
    def test_valid_payload(self):
        p = ProposalGeneratedPayload(
            proposal_id="abc-123",
            signal_type="A",
            change_tier=2,
            ontology_module="finance",
        )
        assert p.signal_type == "A"
        assert p.change_tier == 2

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ProposalGeneratedPayload(
                proposal_id="abc",
                signal_type="A",
                change_tier=1,
                ontology_module="test",
                extra_field="bad",
            )


class TestProposalDecidedPayload:
    def test_valid_payload(self):
        p = ProposalDecidedPayload(
            proposal_id="abc-123",
            decision="approved",
            reviewer_hash="sha256hex",
        )
        assert p.decision == "approved"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ProposalDecidedPayload(
                proposal_id="abc",
                decision="approved",
                reviewer_hash="x",
                secret="bad",
            )


class TestProposalViewedPayload:
    def test_valid_payload(self):
        p = ProposalViewedPayload(proposal_id="abc-123", change_tier=3)
        assert p.change_tier == 3


class TestPayloadModelRegistry:
    def test_proposal_generated_registered(self):
        assert payload_model_for("proposal_generated") is ProposalGeneratedPayload

    def test_proposal_decided_registered(self):
        assert payload_model_for("proposal_decided") is ProposalDecidedPayload

    def test_proposal_viewed_registered(self):
        assert payload_model_for("proposal_viewed") is ProposalViewedPayload


class TestValidatePayload:
    def test_proposal_generated_validates(self):
        result = validate_payload_for_event_type("proposal_generated", {
            "proposal_id": "abc",
            "signal_type": "B",
            "change_tier": 1,
            "ontology_module": "legal",
        })
        assert isinstance(result, ProposalGeneratedPayload)

    def test_proposal_generated_rejects_missing_field(self):
        with pytest.raises(ValidationError):
            validate_payload_for_event_type("proposal_generated", {
                "proposal_id": "abc",
                # missing signal_type, change_tier, ontology_module
            })
