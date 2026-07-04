"""Tests for calibration API routes (Chunk 49, D394–D397)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.ontology.models import CalibrationRecord, TrustScore


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_trust_score(tier: int = 1, **kwargs) -> TrustScore:
    defaults = {
        "tier": tier,
        "trust_score": 0.85,
        "autonomy_threshold": 0.95,
        "autonomy_enabled": False,
        "window_size": 50,
        "min_reviews_for_calibration": 50,
        "risk_tolerance": 0.95,
        "total_decisions": 60,
        "regression_detected": False,
        "last_computed_at": None,
    }
    defaults.update(kwargs)
    return TrustScore(**defaults)


def _make_calibration_record(tier: int = 1) -> CalibrationRecord:
    return CalibrationRecord(
        change_tier=tier,
        confidence_band_low=0.5,
        confidence_band_high=0.6,
        approval_rate=0.9,
        sample_count=10,
        trust_score=0.85,
        autonomy_threshold=0.95,
        autonomy_enabled=False,
        window_size=50,
        risk_tolerance=0.95,
    )


_GET_TRUST_PATCH = "src.api.calibration_routes.get_trust_score_for_tier"
_GET_RECORDS_PATCH = "src.api.calibration_routes.get_calibration_records_for_tier"


class TestDashboardGet:
    def test_dashboard_returns_aggregated_payload(self, client):
        ts = _make_trust_score(tier=1)
        records = [_make_calibration_record()]
        with (
            patch(_GET_TRUST_PATCH, return_value=ts),
            patch(_GET_RECORDS_PATCH, return_value=records),
        ):
            resp = client.get("/api/ontology/calibration/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "tiers" in data
        assert len(data["tiers"]) == 3


class TestBandsGet:
    def test_bands_returns_list(self, client):
        records = [_make_calibration_record()]
        with patch(_GET_RECORDS_PATCH, return_value=records):
            resp = client.get("/api/ontology/calibration/bands/1")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_bands_invalid_tier(self, client):
        resp = client.get("/api/ontology/calibration/bands/4")
        assert resp.status_code == 422


class TestTrustGet:
    def test_trust_returns_state(self, client):
        ts = _make_trust_score(tier=2)
        with patch(_GET_TRUST_PATCH, return_value=ts):
            resp = client.get("/api/ontology/calibration/trust/2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == 2

    def test_trust_404_when_no_data(self, client):
        with patch(_GET_TRUST_PATCH, return_value=None):
            resp = client.get("/api/ontology/calibration/trust/1")
        assert resp.status_code == 404

    def test_trust_invalid_tier(self, client):
        resp = client.get("/api/ontology/calibration/trust/0")
        assert resp.status_code == 422


class TestConfigPatch:
    def test_config_patch_updates_successfully(self, client):
        mock_row = MagicMock()
        mock_row.tier = 1
        mock_row.trust_score = 0.85
        mock_row.autonomy_threshold = 0.95
        mock_row.autonomy_enabled = False
        mock_row.window_size = 50
        mock_row.min_reviews_for_calibration = 50
        mock_row.risk_tolerance = 0.90
        mock_row.total_decisions = 60
        mock_row.regression_detected = False
        mock_row.last_computed_at = None

        with patch("src.api.calibration_routes.TrustScoreRow") as mock_ts_row_cls:
            mock_query = MagicMock()
            mock_query.filter.return_value.first.return_value = mock_row

            def _fake_get_db():
                mock_db = MagicMock()
                mock_db.query.return_value = mock_query
                yield mock_db

            from src.shared.database import get_db as real_get_db
            app.dependency_overrides[real_get_db] = _fake_get_db
            try:
                resp = client.patch(
                    "/api/ontology/calibration/config/1",
                    json={"risk_tolerance": 0.90},
                )
            finally:
                app.dependency_overrides.pop(real_get_db, None)
        assert resp.status_code == 200

    def test_config_patch_tier_range_guard_0(self, client):
        resp = client.patch(
            "/api/ontology/calibration/config/0",
            json={"risk_tolerance": 0.90},
        )
        assert resp.status_code == 422

    def test_config_patch_tier_range_guard_4(self, client):
        resp = client.patch(
            "/api/ontology/calibration/config/4",
            json={"risk_tolerance": 0.90},
        )
        assert resp.status_code == 422

    def test_config_patch_risk_tolerance_too_low(self, client):
        resp = client.patch(
            "/api/ontology/calibration/config/1",
            json={"risk_tolerance": 0.50},
        )
        assert resp.status_code == 422

    def test_config_patch_window_size_too_small(self, client):
        resp = client.patch(
            "/api/ontology/calibration/config/1",
            json={"window_size": 5},
        )
        assert resp.status_code == 422

    def test_config_patch_min_reviews_too_small(self, client):
        resp = client.patch(
            "/api/ontology/calibration/config/1",
            json={"min_reviews_for_calibration": 3},
        )
        assert resp.status_code == 422
