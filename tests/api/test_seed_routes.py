"""Tests for seed management API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.discovery.seed_models import (
    ProvisioningResult,
    SeedReference,
    SeedStatus,
)

client = TestClient(app)


def test_get_industries():
    """GET /api/discovery/seed/industries returns 8 profiles."""
    resp = client.get("/api/discovery/seed/industries")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 8
    ids = [p["industry_id"] for p in data]
    assert "financial_services" in ids
    assert "general" in ids


def test_get_sources_all():
    """GET /api/discovery/seed/sources returns all 11 sources."""
    resp = client.get("/api/discovery/seed/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 11


def test_get_sources_filtered():
    """GET /api/discovery/seed/sources?industry_id=general returns filtered sources."""
    resp = client.get("/api/discovery/seed/sources?industry_id=general")
    assert resp.status_code == 200
    data = resp.json()
    # general: universal (2) + required (1) + recommended (2) = 5, deduplicated
    source_ids = [s["id"] for s in data]
    assert "schema_org_base" in source_ids
    assert "prov_o_core" in source_ids
    assert "fibo_legal_entities" in source_ids


def test_provision_preview():
    """POST /api/discovery/seed/provision with confirmed=false returns preview."""
    resp = client.post(
        "/api/discovery/seed/provision",
        json={"industry_id": "general", "confirmed": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "preview"
    assert data["industry_profile"] == "general"
    assert "sources" in data
    assert "needs_download" in data
    assert "already_present" in data


def test_provision_confirmed():
    """POST /api/discovery/seed/provision with confirmed=true runs provisioning."""
    mock_result = ProvisioningResult(
        industry_profile="general",
        sources_downloaded=[],
        sources_already_present=["schema_org_base", "prov_o_core", "fibo_legal_entities"],
        sources_failed=[],
        total_files=3,
        errors=[],
    )
    mock_ref = SeedReference(
        entity_types=[],
        relationships=[],
        source_files=[],
        industry_profile="general",
        registry_version="1.0.0",
        total_entity_types=10,
        total_relationships=5,
    )

    with (
        patch("src.api.seed_routes.provision_seeds", new_callable=AsyncMock, return_value=(mock_result, mock_ref)),
        patch("src.api.seed_routes._update_industry_profile"),
    ):
        resp = client.post(
            "/api/discovery/seed/provision",
            json={"industry_id": "general", "confirmed": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["total_entity_types"] == 10


def test_seed_status():
    """GET /api/discovery/seed/status returns status info."""
    resp = client.get("/api/discovery/seed/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data


def test_get_reference_no_profile():
    """GET /api/discovery/seed/reference returns error when no profile set."""
    with patch("src.api.seed_routes.load_discovery_config", return_value={"seed": {"industry_profile": ""}}):
        resp = client.get("/api/discovery/seed/reference")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
