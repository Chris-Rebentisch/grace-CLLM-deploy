"""Tests for Chunk 48 proposal execute/preview/batch routes (CP4)."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.ontology.change_executor import ExecutionResult
from src.ontology.models import ProposalStatus

_FAKE_ID = uuid4()
_ROUTES_MOD = "src.api.proposal_routes"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_proposal(status: ProposalStatus = ProposalStatus.APPROVED):
    from src.ontology.evidence_bundle import EvidenceBundle
    from src.ontology.models import ProposalType, SchemaProposal, classify_tier

    return SchemaProposal(
        id=_FAKE_ID,
        proposal_type=ProposalType.ADD_ENTITY_TYPE,
        change_tier=classify_tier(ProposalType.ADD_ENTITY_TYPE),
        kgcl_command="create class 'TestType'",
        proposed_diff={},
        evidence=EvidenceBundle(
            source_signal_ids=[],
            signal_type="A",
            signal_strength=0.8,
            affected_entity_types=["Person"],
            ontology_module="default",
        ),
        raw_confidence=1.0,
        status=status,
        current_schema_version_id=uuid4(),
    )


class TestExecuteRoute:
    def test_execute_returns_execution_result(self, client) -> None:
        """Execute route returns ExecutionResult with correct shape."""
        proposal = _make_proposal()
        result = ExecutionResult(success=True, version_id=uuid4())

        with (
            patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_ROUTES_MOD}.apply_proposal", new_callable=AsyncMock, return_value=result),
        ):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/execute")

        assert resp.status_code == 200
        body = resp.json()
        assert "success" in body
        assert body["success"] is True

    def test_execute_409_on_non_approved(self, client) -> None:
        proposal = _make_proposal(status=ProposalStatus.PENDING)

        with patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/execute")

        assert resp.status_code == 409

    def test_execute_404_on_missing(self, client) -> None:
        with patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=None):
            resp = client.post(f"/api/ontology/proposals/{uuid4()}/execute")

        assert resp.status_code == 404


def _preview_active_version():
    from src.ontology.models import OntologyVersion, VersionSource

    return OntologyVersion(
        version_number=1,
        schema_json={"entity_types": {}, "relationships": {}},
        schema_modules={"default": {}},
        hash_chain="abc",
        source=VersionSource.MANUAL,
    )


@pytest.fixture
def preview_client():
    """TestClient with get_db overridden — preview must never hit real services."""
    from src.shared.database import get_db

    mock_db = MagicMock()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            yield c, mock_db
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestPreviewRoute:
    def test_preview_returns_diff(self, preview_client) -> None:
        """Preview returns parsed change + diff without persisting."""
        client, mock_db = preview_client
        proposal = _make_proposal()

        with (
            patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_ROUTES_MOD}.get_active_version", return_value=_preview_active_version()),
            patch(f"{_ROUTES_MOD}._get_graph_client", side_effect=RuntimeError("no graph")),
        ):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/preview")

        assert resp.status_code == 200
        body = resp.json()
        assert "parsed" in body
        assert "diff" in body

    def test_preview_usage_counts_with_mock_graph(self, preview_client) -> None:
        """F-0040 / ISS-0053: preview carries per-type usage counts."""
        client, mock_db = preview_client
        proposal = _make_proposal()
        mock_db.execute.return_value.scalar.return_value = 2

        graph = MagicMock()
        graph.execute_cypher = AsyncMock(return_value={"result": [{"c": 7}]})
        graph.aclose = AsyncMock()

        with (
            patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_ROUTES_MOD}.get_active_version", return_value=_preview_active_version()),
            patch(f"{_ROUTES_MOD}._get_graph_client", return_value=graph),
        ):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/preview")

        assert resp.status_code == 200
        body = resp.json()
        assert body["affected_entity_types"] == ["TestType"]
        usage = body["usage"]
        assert usage["graph_available"] is True
        entry = usage["by_type"]["TestType"]
        assert entry["instance_count"] == 7
        assert entry["inbound_relationship_count"] == 7
        assert entry["open_claim_count"] == 2

    def test_preview_tolerates_absent_graph(self, preview_client) -> None:
        """F-0040 / ISS-0053: absent graph -> null counts, not a 500."""
        client, mock_db = preview_client
        proposal = _make_proposal()
        mock_db.execute.return_value.scalar.return_value = 4

        with (
            patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_ROUTES_MOD}.get_active_version", return_value=_preview_active_version()),
            patch(f"{_ROUTES_MOD}._get_graph_client", side_effect=RuntimeError("down")),
        ):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/preview")

        assert resp.status_code == 200
        usage = resp.json()["usage"]
        assert usage["graph_available"] is False
        entry = usage["by_type"]["TestType"]
        assert entry["instance_count"] is None
        assert entry["inbound_relationship_count"] is None
        assert entry["open_claim_count"] == 4

    def test_preview_tier_legend_and_cq_gate_note(self, preview_client) -> None:
        """F-0040 / ISS-0053: tier obligations legend + CQ-gate note in preview."""
        client, _mock_db = preview_client
        proposal = _make_proposal()

        with (
            patch(f"{_ROUTES_MOD}.get_proposal_by_id", return_value=proposal),
            patch(f"{_ROUTES_MOD}.get_active_version", return_value=_preview_active_version()),
            patch(f"{_ROUTES_MOD}._get_graph_client", side_effect=RuntimeError("down")),
        ):
            resp = client.post(f"/api/ontology/proposals/{_FAKE_ID}/preview")

        body = resp.json()
        legend = body["change_tier_legend"]
        assert set(legend["tiers"].keys()) == {"1", "2", "3"}
        assert legend["current"] == legend["tiers"][str(body["change_tier"])]
        assert "human-reviewed" in legend["tiers"]["3"].lower() or "always" in legend["tiers"]["3"].lower()
        assert body["cq_gate"]["runs_at"] == "execute"
        assert "execute" in body["cq_gate"]["note"]

    def test_preview_admitted_as_readonly(self) -> None:
        """Preview route is in READONLY_ROUTES."""
        from src.mcp_server.server import READONLY_ROUTES

        assert (
            "POST",
            "/api/ontology/proposals/{proposal_id}/preview",
        ) in READONLY_ROUTES


class TestBatchTriggerRoute:
    def test_batch_trigger_returns_accepted(self, client) -> None:
        """Batch-trigger returns 202 + batch_id (spec §routes / DV2)."""
        from src.api.proposal_routes import _batch_in_progress

        _batch_in_progress.clear()

        with patch(f"{_ROUTES_MOD}.subprocess.Popen"):
            resp = client.post("/api/ontology/proposals/batch-trigger")

        # Clean up (background thread may still clear the DV1 sentinel on mock `.wait()`).
        _batch_in_progress.clear()

        assert resp.status_code == 202
        body = resp.json()
        assert "batch_id" in body
        assert "status" in body

    def test_batch_trigger_second_request_409_while_locked(self, client) -> None:
        """409 when another batch spawn is marked in-flight before first completes."""

        import src.api.proposal_routes as pr

        pr._batch_in_progress.clear()

        wait_started = threading.Event()
        proceed = threading.Event()

        proc_mock = MagicMock()

        def _blocking_wait(timeout=None):
            wait_started.set()
            proceed.wait(timeout=30)
            return 0

        proc_mock.wait = _blocking_wait

        with patch.object(pr.subprocess, "Popen", return_value=proc_mock):
            resp1 = client.post("/api/ontology/proposals/batch-trigger")
            wait_started.wait(timeout=5)

            resp2 = client.post("/api/ontology/proposals/batch-trigger")

        proceed.set()

        body2 = resp2.json()
        pr._batch_in_progress.clear()

        assert resp1.status_code == 202
        assert resp2.status_code == 409
        assert "already in progress" in body2["detail"].lower()
