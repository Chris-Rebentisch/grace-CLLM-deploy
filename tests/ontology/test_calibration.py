"""Tests for pure calibration computation module (Chunk 49, D394–D396)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.ontology.calibration import (
    RegressionResult,
    TrendResult,
    compute_calibration_bands,
    compute_trust_score,
    detect_regression,
    detect_trend,
)
from src.ontology.models import CalibrationDecision


def _make_decision(
    confidence: float = 0.5,
    decision: str = "approved",
    tier: int = 1,
) -> CalibrationDecision:
    return CalibrationDecision(
        proposal_id=uuid4(),
        change_tier=tier,
        raw_confidence=confidence,
        decision=decision,
        recorded_at=datetime.now(UTC),
    )


# --- Binning tests ---


class TestComputeCalibrationBands:
    def test_empty_input(self):
        bands = compute_calibration_bands([])
        assert len(bands) == 10
        assert all(b.sample_count == 0 for b in bands)
        assert all(b.approval_rate == 0.0 for b in bands)

    def test_single_decision(self):
        decisions = [_make_decision(confidence=0.55, decision="approved")]
        bands = compute_calibration_bands(decisions)
        assert len(bands) == 10
        # Band [0.5, 0.6) should have the decision.
        band_5 = bands[5]
        assert band_5.sample_count == 1
        assert band_5.approval_rate == 1.0

    def test_full_10_bands(self):
        bands = compute_calibration_bands([])
        assert len(bands) == 10
        assert bands[0].band_low == 0.0
        assert bands[0].band_high == 0.1
        assert bands[9].band_low == 0.9
        assert bands[9].band_high == 1.0

    def test_custom_band_width_20_bands(self):
        bands = compute_calibration_bands([], band_width=0.05)
        assert len(bands) == 20
        assert bands[0].band_low == 0.0
        assert bands[0].band_high == 0.05
        assert bands[19].band_low == 0.95
        assert bands[19].band_high == 1.0

    def test_boundary_confidence_1_0(self):
        """Confidence == 1.0 should land in the last band."""
        decisions = [_make_decision(confidence=1.0, decision="approved")]
        bands = compute_calibration_bands(decisions)
        assert bands[9].sample_count == 1

    def test_mixed_decisions_approval_rate(self):
        decisions = [
            _make_decision(confidence=0.55, decision="approved"),
            _make_decision(confidence=0.55, decision="rejected"),
            _make_decision(confidence=0.55, decision="approved"),
            _make_decision(confidence=0.55, decision="approved"),
        ]
        bands = compute_calibration_bands(decisions)
        band_5 = bands[5]
        assert band_5.sample_count == 4
        assert band_5.approval_rate == pytest.approx(0.75)


# --- Trust score tests ---


class TestComputeTrustScore:
    def test_empty_input(self):
        assert compute_trust_score([]) == 0.0

    def test_all_approved(self):
        decisions = [_make_decision(decision="approved") for _ in range(10)]
        assert compute_trust_score(decisions, window_size=10) == 1.0

    def test_all_rejected(self):
        decisions = [_make_decision(decision="rejected") for _ in range(10)]
        assert compute_trust_score(decisions, window_size=10) == 0.0

    def test_mixed_decisions(self):
        decisions = [
            _make_decision(decision="approved"),
            _make_decision(decision="rejected"),
        ]
        assert compute_trust_score(decisions, window_size=10) == pytest.approx(0.5)

    def test_rolling_window_boundary(self):
        """Window is applied to trailing decisions."""
        old = [_make_decision(decision="rejected") for _ in range(100)]
        recent = [_make_decision(decision="approved") for _ in range(50)]
        score = compute_trust_score(old + recent, window_size=50)
        assert score == 1.0

    def test_window_larger_than_data(self):
        decisions = [_make_decision(decision="approved") for _ in range(3)]
        score = compute_trust_score(decisions, window_size=50)
        assert score == 1.0


# --- Regression detection tests ---


class TestDetectRegression:
    def test_sparse_band_returns_no_regression(self):
        result = detect_regression(
            historical_rate=0.9,
            recent_rate=0.5,
            recent_n=3,
            sparse_band_floor=5,
        )
        assert result.regression_detected is False
        assert result.reason == "sparse"

    def test_known_regression(self):
        """High historical rate with low recent rate should flag regression."""
        result = detect_regression(
            historical_rate=0.95,
            recent_rate=0.5,
            recent_n=50,
            sensitivity=0.10,
        )
        assert result.regression_detected is True
        assert result.reason == "ok"

    def test_no_regression_when_rates_close(self):
        result = detect_regression(
            historical_rate=0.90,
            recent_rate=0.88,
            recent_n=100,
            sensitivity=0.10,
        )
        assert result.regression_detected is False
        assert result.reason == "ok"

    def test_wilson_ci_bounds(self):
        result = detect_regression(
            historical_rate=0.90,
            recent_rate=0.80,
            recent_n=20,
            sensitivity=0.10,
        )
        assert 0.0 <= result.lower_ci <= result.upper_ci <= 1.0


# --- Mann-Kendall trend tests ---


class TestDetectTrend:
    def test_insufficient_data(self):
        result = detect_trend([0.5, 0.6, 0.7])
        assert result.direction == "insufficient_data"
        assert result.p_value is None
        assert result.tau is None

    def test_monotonic_decreasing(self):
        series = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
        result = detect_trend(series)
        assert result.direction == "decreasing"
        assert result.p_value is not None
        assert result.tau is not None
        assert result.tau < 0

    def test_no_trend(self):
        series = [0.5, 0.6, 0.5, 0.6, 0.5, 0.6, 0.5, 0.6]
        result = detect_trend(series)
        assert result.direction == "no_trend"

    def test_monotonic_increasing(self):
        series = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        result = detect_trend(series)
        assert result.direction == "increasing"
        assert result.tau is not None
        assert result.tau > 0
