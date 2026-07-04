"""Tests for CLI calibration updater (Chunk 49, D394–D396, D246 mirror)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.ontology.calibration_updater import run_updater
from src.ontology.models import CalibrationDecision, CalibrationRecord, TrustScore


def _make_decision(
    tier: int = 1,
    confidence: float = 0.5,
    decision: str = "approved",
) -> CalibrationDecision:
    return CalibrationDecision(
        proposal_id=uuid4(),
        change_tier=tier,
        raw_confidence=confidence,
        decision=decision,
        recorded_at=datetime.now(UTC),
    )


def _make_trust_score(tier: int = 1, **kwargs) -> TrustScore:
    defaults = {
        "tier": tier,
        "trust_score": 0.0,
        "autonomy_threshold": 0.95,
        "autonomy_enabled": False,
        "window_size": 50,
        "min_reviews_for_calibration": 50,
        "risk_tolerance": 0.95,
        "total_decisions": 0,
        "regression_detected": False,
        "last_computed_at": None,
    }
    defaults.update(kwargs)
    return TrustScore(**defaults)


def _make_cal_record(tier: int = 1, band_low: float = 0.0, band_high: float = 0.1, approval_rate: float = 0.9) -> CalibrationRecord:
    return CalibrationRecord(
        change_tier=tier,
        confidence_band_low=band_low,
        confidence_band_high=band_high,
        approval_rate=approval_rate,
        sample_count=10,
        trust_score=0.9,
        autonomy_threshold=0.95,
        autonomy_enabled=False,
        window_size=50,
        risk_tolerance=0.95,
    )


_DB_MODULE = "src.ontology.calibration_updater"


class TestCalibrationUpdater:
    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_happy_path(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        # Use enough decisions to narrow the Wilson CI so no false regression.
        decisions = [_make_decision(tier=1, confidence=0.55, decision="approved") for _ in range(60)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert 1 in results
        assert results[1]["decisions_count"] == 60
        assert results[1]["trust_score"] == 1.0
        assert results[1]["regression_detected"] is False
        mock_del.assert_called_once_with(db, 1)
        assert mock_create_rec.call_count == 10  # 10 bands
        mock_upsert.assert_called_once()

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_regression_flag_set(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """High prior approval rate + low recent rate → regression."""
        decisions = [_make_decision(tier=1, confidence=0.55, decision="rejected") for _ in range(20)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        # Prior band had 95% approval rate.
        mock_get_prior.return_value = [_make_cal_record(tier=1, band_low=0.5, band_high=0.6, approval_rate=0.95)]

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert results[1]["regression_detected"] is True
        _, kwargs = mock_upsert.call_args
        assert kwargs["regression_detected"] is True

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_idempotency(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        decisions = [_make_decision(tier=1) for _ in range(5)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        mock_get_prior.return_value = []

        db = MagicMock()
        r1 = run_updater(db, tiers=[1])
        r2 = run_updater(db, tiers=[1])

        assert r1[1]["trust_score"] == r2[1]["trust_score"]
        assert r1[1]["bands_count"] == r2[1]["bands_count"]

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_empty_decisions_cold_start(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        mock_get_dec.return_value = []
        mock_get_trust.return_value = None  # Cold start
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert results[1]["decisions_count"] == 0
        assert results[1]["trust_score"] == 0.0
        assert results[1]["regression_detected"] is False

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_dry_run_no_writes(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        decisions = [_make_decision(tier=1) for _ in range(10)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db, tiers=[1], dry_run=True)

        assert results[1]["dry_run"] is True
        mock_del.assert_not_called()
        mock_create_rec.assert_not_called()
        mock_upsert.assert_not_called()

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_per_tier_isolation(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """Running for tier 1 only does not read/write tier 2 data."""
        decisions_t1 = [_make_decision(tier=1)]
        mock_get_dec.return_value = decisions_t1
        mock_get_trust.return_value = _make_trust_score(tier=1)
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert 1 in results
        assert 2 not in results
        # get_calibration_decisions_for_tier should only be called with tier=1.
        mock_get_dec.assert_called_once_with(db, 1)

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_optimistic_default_for_new_bands(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """New bands with no prior match use optimistic default 1.0 → no regression with enough samples."""
        decisions = [_make_decision(tier=1, confidence=0.55, decision="approved") for _ in range(60)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        mock_get_prior.return_value = []  # No prior bands → optimistic default

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert results[1]["regression_detected"] is False

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_prior_rates_lookup(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """Prior rates are looked up correctly by (band_low, band_high)."""
        decisions = [_make_decision(tier=1, confidence=0.55, decision="approved") for _ in range(60)]
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score()
        # Prior band [0.5, 0.6) with 90% approval - matches the bin where decisions land.
        mock_get_prior.return_value = [_make_cal_record(tier=1, band_low=0.5, band_high=0.6, approval_rate=0.9)]

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        # All 60 decisions approved in same band → 100% recent rate vs 90% prior → no regression
        # (Wilson CI lower bound with 60 samples is ~0.94, above 0.90-0.10=0.80 threshold).
        assert results[1]["regression_detected"] is False

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_all_tiers_default(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """Default tiers run all 3."""
        mock_get_dec.return_value = []
        mock_get_trust.return_value = None
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db)

        assert set(results.keys()) == {1, 2, 3}

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_mixed_decisions_trust_score(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        """Trust score reflects mix of approved/rejected."""
        decisions = (
            [_make_decision(tier=1, decision="approved") for _ in range(7)]
            + [_make_decision(tier=1, decision="rejected") for _ in range(3)]
        )
        mock_get_dec.return_value = decisions
        mock_get_trust.return_value = _make_trust_score(window_size=10)
        mock_get_prior.return_value = []

        db = MagicMock()
        results = run_updater(db, tiers=[1])

        assert results[1]["trust_score"] == pytest.approx(0.7)

    @patch(f"{_DB_MODULE}.get_calibration_decisions_for_tier")
    @patch(f"{_DB_MODULE}.get_trust_score_for_tier")
    @patch(f"{_DB_MODULE}.get_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.delete_calibration_records_for_tier")
    @patch(f"{_DB_MODULE}.create_calibration_record")
    @patch(f"{_DB_MODULE}.upsert_trust_score")
    def test_observation_time_passed_to_upsert(self, mock_upsert, mock_create_rec, mock_del, mock_get_prior, mock_get_trust, mock_get_dec):
        mock_get_dec.return_value = [_make_decision()]
        mock_get_trust.return_value = _make_trust_score()
        mock_get_prior.return_value = []
        obs = datetime(2026, 1, 1, tzinfo=UTC)

        db = MagicMock()
        run_updater(db, tiers=[1], observation_time=obs)

        _, kwargs = mock_upsert.call_args
        assert kwargs["last_computed_at"] == obs
