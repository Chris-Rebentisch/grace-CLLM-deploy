"""Tests for FastAPI merge route endpoints."""

from unittest.mock import AsyncMock, patch

import pytest

from src.discovery.cq_merge import _merge_runs
from src.discovery.merge_models import MergeRun


@pytest.fixture(autouse=True)
def clear_merge_runs():
    """Clear in-memory merge runs before and after each test."""
    _merge_runs.clear()
    yield
    _merge_runs.clear()


@pytest.fixture()
def client():
    """Create a FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


def test_merge_cqs_returns_run_id(client):
    """POST /api/discovery/merge-cqs with dry_run returns run_id."""
    # Mock the merge pipeline to avoid real DB/LLM calls
    mock_run = MergeRun(status="completed", total_clusters=3, total_singletons=1)

    with patch("src.discovery.cq_merge.run_merge_pipeline", new_callable=AsyncMock, return_value=mock_run):
        resp = client.post("/api/discovery/merge-cqs", json={"dry_run": True})

    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["dry_run"] is True


def test_merge_status_not_found(client):
    """GET with invalid run_id -> error."""
    resp = client.get("/api/discovery/merge-status/nonexistent-run-id")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"] == "Run not found"


def test_merge_status_valid(client):
    """Store a MergeRun in _merge_runs, GET returns it."""
    run = MergeRun(status="completed", total_cqs_input=10, total_clusters=3)
    _merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/merge-status/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run.run_id
    assert data["status"] == "completed"
    assert data["total_cqs_input"] == 10
    assert data["total_clusters"] == 3


def test_merge_results_valid(client):
    """GET /merge-results returns data."""
    run = MergeRun(
        status="completed",
        total_cqs_input=15,
        total_clusters=5,
        total_singletons=2,
        total_gap_fills=3,
        mean_cluster_size=2.6,
        agreement_distribution={"high": 2, "medium": 2, "low": 1},
        quality_distribution={"clean": 3, "review": 1, "suspect": 1},
    )
    _merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/merge-results/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cqs_input"] == 15
    assert data["total_gap_fills"] == 3
    assert data["agreement_distribution"]["high"] == 2
    assert data["quality_distribution"]["clean"] == 3


def test_merge_hierarchy_endpoint(client):
    """GET /merge-results/{id}/hierarchy returns hierarchy JSON."""
    hierarchy = {
        "domain_groups": [
            {"domain": "insurance", "sub_domains": [{"name": "policy_types", "cq_ids": ["cq-1", "cq-2"]}]}
        ],
        "cross_domain_links": [],
    }
    run = MergeRun(status="completed", hierarchy_json=hierarchy)
    _merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/merge-results/{run.run_id}/hierarchy")
    assert resp.status_code == 200
    data = resp.json()
    assert "domain_groups" in data
    assert data["domain_groups"][0]["domain"] == "insurance"
    assert len(data["domain_groups"][0]["sub_domains"][0]["cq_ids"]) == 2
