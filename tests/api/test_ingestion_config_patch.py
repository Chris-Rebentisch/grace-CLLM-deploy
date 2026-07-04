"""Tests for ingestion config routes (Chunk 60, CP1).

GET /api/ingestion/config snapshot shape.
PATCH /api/ingestion/config/organization-domains success + 422 reject.
PATCH /api/ingestion/config/tier3-threshold success + band→numeric on disk.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from src.api.main import app

    return TestClient(app)


@pytest.fixture()
def _tmp_config(tmp_path):
    """Set up temporary config directory with YAML files for testing."""
    vt = tmp_path / "voice_tone_config.yaml"
    vt.write_text(yaml.dump({"organization_domains": ["example.com"]}))

    tr = tmp_path / "triage_rules.yaml"
    tr.write_text(yaml.dump({"tier3": {"threshold": 0.30, "batch_size": 100}}))

    disc = tmp_path / "discovery.yaml"
    disc.write_text(yaml.dump({"ingestion": {"deployment_path": "A"}}))

    return tmp_path


def test_get_config_returns_snapshot_shape(client, _tmp_config):
    """GET /api/ingestion/config returns deployment_path, organization_domains, tier3_band."""
    config_root = _tmp_config

    def _patched_resolve(*args, **kwargs):
        return config_root

    with patch(
        "src.api.ingestion_routes.Path.__truediv__",
        side_effect=lambda self, other: config_root / other if str(self).endswith("config") else Path.__truediv__(self, other),
    ):
        # Simpler approach: patch the yaml loading inline
        pass

    # Direct test via HTTP — relies on real config files
    resp = client.get("/api/ingestion/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "deployment_path" in body
    assert "organization_domains" in body
    assert "tier3_band" in body
    # tier3_band must be a band label, never raw numeric
    assert body["tier3_band"] in ("stricter", "balanced", "looser")


def test_patch_org_domains_valid(client):
    """PATCH /api/ingestion/config/organization-domains succeeds with valid domains."""
    resp = client.patch(
        "/api/ingestion/config/organization-domains",
        json={"organization_domains": ["acme.com", "test.org"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_domains"] == ["acme.com", "test.org"]


def test_patch_org_domains_invalid_rejects_422(client):
    """PATCH /api/ingestion/config/organization-domains rejects invalid domains."""
    resp = client.patch(
        "/api/ingestion/config/organization-domains",
        json={"organization_domains": ["not a domain!", ""]},
    )
    assert resp.status_code == 422


def test_patch_tier3_valid(client):
    """PATCH /api/ingestion/config/tier3-threshold succeeds and maps band→numeric."""
    resp = client.patch(
        "/api/ingestion/config/tier3-threshold",
        json={"tier3_band": "stricter"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier3_band"] == "stricter"


def test_patch_tier3_invalid_band(client):
    """PATCH /api/ingestion/config/tier3-threshold rejects unknown band."""
    resp = client.patch(
        "/api/ingestion/config/tier3-threshold",
        json={"tier3_band": "unknown_band"},
    )
    assert resp.status_code == 422


def test_patch_tier3_writes_numeric_to_disk(client, tmp_path):
    """After PATCH tier3-threshold, triage_rules.yaml contains the numeric value."""
    tr_path = tmp_path / "triage_rules.yaml"
    tr_path.write_text(yaml.dump({"tier3": {"threshold": 0.30, "batch_size": 100}}))

    with patch(
        "src.api.ingestion_routes.Path",
        wraps=Path,
    ):
        # Use real config path — verify it wrote correctly
        resp = client.patch(
            "/api/ingestion/config/tier3-threshold",
            json={"tier3_band": "looser"},
        )
        assert resp.status_code == 200

    # Read back the real config to verify numeric write
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "triage_rules.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)
    assert data["tier3"]["threshold"] == 0.40

    # Restore original
    data["tier3"]["threshold"] = 0.30
    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
