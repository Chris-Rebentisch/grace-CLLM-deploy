"""Tests for FastAPI schema route endpoints."""

from unittest.mock import AsyncMock, patch

import pytest

from src.discovery.schema_extractor import _schema_runs
from src.discovery.schema_merge import _schema_merge_runs
from src.discovery.schema_merge_models import SchemaMergeRun
from src.discovery.schema_models import SchemaExtractionRun


@pytest.fixture(autouse=True)
def clear_runs():
    """Clear in-memory runs before and after each test."""
    _schema_runs.clear()
    _schema_merge_runs.clear()
    yield
    _schema_runs.clear()
    _schema_merge_runs.clear()


@pytest.fixture()
def client():
    """Create a FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


def test_extract_returns_run_id(client):
    """POST /api/discovery/schema/extract with dry_run returns run_id."""
    mock_run = SchemaExtractionRun(status="completed")

    with patch(
        "src.discovery.schema_extractor.run_schema_extraction",
        new_callable=AsyncMock,
        return_value=mock_run,
    ):
        resp = client.post(
            "/api/discovery/schema/extract", json={"dry_run": True}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["dry_run"] is True


def test_extraction_status_not_found(client):
    """GET with invalid run_id -> error."""
    resp = client.get("/api/discovery/schema/extraction-status/nonexistent")
    assert resp.status_code == 200
    assert resp.json()["error"] == "Run not found"


def test_extraction_status_valid(client):
    """Store an extraction run, GET returns it."""
    run = SchemaExtractionRun(status="completed", total_entity_types=10)
    _schema_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/schema/extraction-status/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["total_entity_types"] == 10


def test_merge_returns_run_id(client):
    """POST /api/discovery/schema/merge with dry_run returns run_id."""
    mock_run = SchemaMergeRun(status="completed", merged_entity_types=5)

    with patch(
        "src.discovery.schema_merge.run_schema_merge",
        new_callable=AsyncMock,
        return_value=mock_run,
    ):
        resp = client.post(
            "/api/discovery/schema/merge", json={"dry_run": True}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["dry_run"] is True


def test_merge_status_valid(client):
    """Store a merge run, GET returns it."""
    run = SchemaMergeRun(
        status="completed",
        merged_entity_types=8,
        cq_coverage_rate=0.85,
    )
    _schema_merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/schema/merge-status/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["merged_entity_types"] == 8


def test_seed_schema_endpoint(client):
    """GET /api/discovery/schema/seed-schema returns SeedSchema JSON."""
    run = SchemaMergeRun(
        status="completed",
        seed_schema_json={
            "entity_types": [{"name": "Policy", "provenance": "3pass_novel"}],
            "relationships": [],
            "coverage_matrix": [],
            "provenance_summary": {"3pass_novel": 1},
            "quality_metrics": {"cq_coverage_rate": 0.9},
            "gap_report": {},
        },
    )
    _schema_merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/schema/seed-schema/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "entity_types" in data
    assert data["entity_types"][0]["name"] == "Policy"


def test_coverage_endpoint(client):
    """GET /api/discovery/schema/coverage returns coverage matrix."""
    run = SchemaMergeRun(
        status="completed",
        seed_schema_json={
            "coverage_matrix": [
                {"cq_id": "abc12345", "coverage_status": "covered"},
                {"cq_id": "def67890", "coverage_status": "uncovered"},
            ],
        },
    )
    _schema_merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/schema/coverage/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["coverage_matrix"]) == 2


def test_provenance_endpoint(client):
    """GET /api/discovery/schema/provenance returns distribution."""
    run = SchemaMergeRun(
        status="completed",
        provenance_distribution={"seed+3pass": 3, "2pass_novel": 5},
        richness_distribution={"simple": 4, "attributed": 2, "reified": 1},
        cq_coverage_rate=0.82,
        cross_pass_agreement_rate=0.75,
    )
    _schema_merge_runs[run.run_id] = run

    resp = client.get(f"/api/discovery/schema/provenance/{run.run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provenance_distribution"]["seed+3pass"] == 3
    assert data["richness_distribution"]["simple"] == 4
    assert data["cq_coverage_rate"] == 0.82
