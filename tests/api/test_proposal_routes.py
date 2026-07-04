"""Tests for proposal API routes (CP4, D387/D389, Chunk 47)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.ontology.evidence_bundle import EvidenceBundle
from src.ontology.models import (
    HumanDecision,
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaProposal,
    SignalType,
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_proposal(**overrides) -> SchemaProposal:
    defaults = {
        "id": uuid4(),
        "created_at": datetime.now(UTC),
        "proposal_type": ProposalType.ADD_ENTITY_TYPE,
        "change_tier": 2,
        "kgcl_command": "create class TestEntity",
        "proposed_diff": {},
        "evidence": EvidenceBundle(
            source_signal_ids=[uuid4()],
            signal_type="A",
            signal_strength=0.75,
            affected_entity_types=["TestEntity"],
            ontology_module="test",
        ),
        "signal_type": SignalType.SIGNAL_A,
        "raw_confidence": 0.5,
        "priority": ProposalPriority.MEDIUM,
        "status": ProposalStatus.PENDING,
        "current_schema_version_id": uuid4(),
        "ontology_module": "test",
        "dedup_hash": "abc123",
        "overflow": False,
        "generated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SchemaProposal(**defaults)


_LIST_PATCH = "src.api.proposal_routes.list_proposals"
_GET_PATCH = "src.api.proposal_routes.get_proposal_by_id"
_UPDATE_PATCH = "src.api.proposal_routes.update_proposal_decision"
_WRITE_EVENT_PATCH = "src.elicitation.event_writer.write_event"


class TestListProposals:
    def test_list_returns_items(self, client):
        p = _make_proposal()
        with patch(_LIST_PATCH, return_value=[p]):
            resp = client.get("/api/ontology/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 1

    def test_list_tier_filter(self, client):
        with patch(_LIST_PATCH, return_value=[]) as mock_list:
            resp = client.get("/api/ontology/proposals?tier=3")
        assert resp.status_code == 200
        _, kwargs = mock_list.call_args
        assert kwargs.get("change_tier") == 3

    def test_list_status_filter(self, client):
        with patch(_LIST_PATCH, return_value=[]) as mock_list:
            resp = client.get("/api/ontology/proposals?status=pending")
        assert resp.status_code == 200

    def test_list_module_filter(self, client):
        with patch(_LIST_PATCH, return_value=[]) as mock_list:
            resp = client.get("/api/ontology/proposals?ontology_module=finance")
        assert resp.status_code == 200

    def test_list_cursor_pagination(self, client):
        proposals = [_make_proposal() for _ in range(26)]
        with patch(_LIST_PATCH, return_value=proposals):
            resp = client.get("/api/ontology/proposals?limit=25")
        data = resp.json()
        assert data["next_cursor"] == "25"
        assert len(data["items"]) == 25

    def test_list_max_limit_enforced(self, client):
        resp = client.get("/api/ontology/proposals?limit=200")
        assert resp.status_code == 422

    def test_list_invalid_status(self, client):
        with patch(_LIST_PATCH, side_effect=None) as mock_list:
            resp = client.get("/api/ontology/proposals?status=invalid_status")
        assert resp.status_code == 422


class TestGetProposal:
    def test_get_returns_proposal(self, client):
        p = _make_proposal()
        with patch(_GET_PATCH, return_value=p):
            resp = client.get(f"/api/ontology/proposals/{p.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kgcl_command"] == "create class TestEntity"

    def test_get_404_for_missing(self, client):
        with patch(_GET_PATCH, return_value=None):
            resp = client.get(f"/api/ontology/proposals/{uuid4()}")
        assert resp.status_code == 404


class TestDecideProposal:
    @patch(_WRITE_EVENT_PATCH)
    def test_decide_approved_flips_status(self, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.APPROVED, human_decision=HumanDecision.APPROVED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "approved", "reviewer": "test_user"},
            )
        assert resp.status_code == 200

    @patch(_WRITE_EVENT_PATCH)
    def test_decide_writes_proposal_decided_elicitation_event(self, mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.APPROVED, human_decision=HumanDecision.APPROVED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "approved", "reviewer": "alice"},
            )
        assert resp.status_code == 200
        mock_write.assert_called_once()
        envelope = mock_write.call_args[0][1]
        assert envelope.event_type == "proposal_decided"
        assert envelope.payload["proposal_id"] == str(p.id)
        assert envelope.payload["decision"] == "approved"

    @patch(_WRITE_EVENT_PATCH)
    def test_decide_modified_computes_distance(self, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(
            status=ProposalStatus.MODIFIED,
            human_decision=HumanDecision.MODIFIED,
            modification_distance=0.1,
        )
        with patch(_GET_PATCH, return_value=p), \
             patch(_UPDATE_PATCH, return_value=updated) as mock_update:
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={
                    "decision": "modified",
                    "reviewer": "test_user",
                    "modified_diff": {"kgcl_command": "create class Renamed"},
                },
            )
        assert resp.status_code == 200
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("modification_distance") is not None

    @patch(_WRITE_EVENT_PATCH)
    def test_decide_deferred_allowed(self, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.DEFERRED, human_decision=HumanDecision.DEFERRED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "deferred", "reviewer": "test_user"},
            )
        assert resp.status_code == 200

    @patch(_WRITE_EVENT_PATCH)
    def test_decide_409_when_not_pending(self, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.APPROVED)
        with patch(_GET_PATCH, return_value=p):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "approved", "reviewer": "test_user"},
            )
        assert resp.status_code == 409

    def test_decide_422_invalid_decision(self, client):
        resp = client.post(
            f"/api/ontology/proposals/{uuid4()}/decide",
            json={"decision": "invalid_decision", "reviewer": "test_user"},
        )
        assert resp.status_code == 422

    @patch(_WRITE_EVENT_PATCH)
    def test_decide_404_for_missing(self, _mock_write, client):
        with patch(_GET_PATCH, return_value=None):
            resp = client.post(
                f"/api/ontology/proposals/{uuid4()}/decide",
                json={"decision": "approved", "reviewer": "test_user"},
            )
        assert resp.status_code == 404


# --- Chunk 49 Calibration Hook Tests (CP4, D394) ---

_CAL_CREATE_PATCH = "src.ontology.database.create_calibration_decision"


class TestCalibrationHook:
    @patch(_WRITE_EVENT_PATCH)
    @patch(_CAL_CREATE_PATCH)
    def test_approved_creates_calibration_decision(self, mock_cal, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.APPROVED, human_decision=HumanDecision.APPROVED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "approved", "reviewer": "test_user"},
            )
        assert resp.status_code == 200
        mock_cal.assert_called_once()
        _, kwargs = mock_cal.call_args
        assert kwargs["decision"] == "approved"

    @patch(_WRITE_EVENT_PATCH)
    @patch(_CAL_CREATE_PATCH)
    def test_rejected_creates_calibration_decision_rejected(self, mock_cal, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.REJECTED, human_decision=HumanDecision.REJECTED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "rejected", "reviewer": "test_user"},
            )
        assert resp.status_code == 200
        mock_cal.assert_called_once()
        _, kwargs = mock_cal.call_args
        assert kwargs["decision"] == "rejected"

    @patch(_WRITE_EVENT_PATCH)
    @patch(_CAL_CREATE_PATCH)
    def test_modified_creates_calibration_decision_approved(self, mock_cal, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(
            status=ProposalStatus.MODIFIED,
            human_decision=HumanDecision.MODIFIED,
            modification_distance=0.1,
        )
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "modified", "reviewer": "test_user", "modified_diff": {"a": 1}},
            )
        assert resp.status_code == 200
        mock_cal.assert_called_once()
        _, kwargs = mock_cal.call_args
        assert kwargs["decision"] == "approved"

    @patch(_WRITE_EVENT_PATCH)
    @patch(_CAL_CREATE_PATCH)
    def test_deferred_skips_calibration(self, mock_cal, _mock_write, client):
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.DEFERRED, human_decision=HumanDecision.DEFERRED)
        with patch(_GET_PATCH, return_value=p), patch(_UPDATE_PATCH, return_value=updated):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "deferred", "reviewer": "test_user"},
            )
        assert resp.status_code == 200
        mock_cal.assert_not_called()

    @patch(_WRITE_EVENT_PATCH)
    def test_calibration_hook_failure_is_log_and_continue(self, _mock_write, client):
        """If calibration INSERT fails, the decide route still returns 200."""
        p = _make_proposal(status=ProposalStatus.PENDING)
        updated = _make_proposal(status=ProposalStatus.APPROVED, human_decision=HumanDecision.APPROVED)
        with (
            patch(_GET_PATCH, return_value=p),
            patch(_UPDATE_PATCH, return_value=updated),
            patch(_CAL_CREATE_PATCH, side_effect=RuntimeError("boom")),
        ):
            resp = client.post(
                f"/api/ontology/proposals/{p.id}/decide",
                json={"decision": "approved", "reviewer": "test_user"},
            )
        assert resp.status_code == 200
