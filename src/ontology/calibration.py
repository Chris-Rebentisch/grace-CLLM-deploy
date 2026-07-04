"""Pure calibration computation functions for earned autonomy (Chunk 49, D394–D396).

No I/O, no database imports. All functions are pure — they take data in and
return computed results. The CLI updater (calibration_updater.py) handles
persistence.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from src.ontology.models import CalibrationBand, CalibrationDecision


class RegressionResult(BaseModel):
    """Result of Wilson-score regression detection (D396)."""

    regression_detected: bool = Field(description="Whether regression was detected")
    lower_ci: float = Field(description="Lower bound of Wilson 95% CI")
    upper_ci: float = Field(description="Upper bound of Wilson 95% CI")
    threshold: float = Field(description="Historical rate minus sensitivity")
    reason: Literal["sparse", "ok"] | None = Field(
        default=None,
        description="'sparse' when sample too small; 'ok' on normal evaluation",
    )


class TrendResult(BaseModel):
    """Result of Mann-Kendall trend analysis (D396)."""

    direction: Literal["increasing", "decreasing", "no_trend", "insufficient_data"] = Field(
        description="Detected trend direction",
    )
    p_value: float | None = Field(default=None, description="Mann-Kendall p-value")
    tau: float | None = Field(default=None, description="Kendall's tau")


def compute_calibration_bands(
    decisions: list[CalibrationDecision],
    band_width: float = 0.10,
) -> list[CalibrationBand]:
    """Fixed-width binning of decisions over [0.0, 1.0] (D394).

    Returns ``ceil(1.0 / band_width)`` bands, each with ``band_low``,
    ``band_high``, ``approval_rate``, ``sample_count``. Empty input
    returns bands with ``sample_count=0``, ``approval_rate=0.0``.
    """
    num_bands = math.ceil(1.0 / band_width)
    bands: list[CalibrationBand] = []

    for i in range(num_bands):
        band_low = round(i * band_width, 10)
        band_high = round((i + 1) * band_width, 10)
        # Clamp the last band to exactly 1.0.
        if band_high > 1.0:
            band_high = 1.0

        in_band = [
            d for d in decisions
            if (band_low <= d.raw_confidence < band_high)
            or (i == num_bands - 1 and d.raw_confidence == 1.0)
        ]

        sample_count = len(in_band)
        if sample_count > 0:
            approved = sum(1 for d in in_band if d.decision == "approved")
            approval_rate = approved / sample_count
        else:
            approval_rate = 0.0

        bands.append(CalibrationBand(
            band_low=band_low,
            band_high=band_high,
            approval_rate=approval_rate,
            sample_count=sample_count,
        ))

    return bands


def compute_trust_score(
    decisions: list[CalibrationDecision],
    window_size: int = 50,
) -> float:
    """Rolling-window trust score (D395).

    Count only over the trailing ``min(len(decisions), window_size)``
    decisions. Returns ``count(decision == "approved") / window`` or 0.0
    for empty input.
    """
    if not decisions:
        return 0.0

    window = min(len(decisions), window_size)
    trailing = decisions[-window:]
    approved = sum(1 for d in trailing if d.decision == "approved")
    return approved / window


def detect_regression(
    historical_rate: float,
    recent_rate: float,
    recent_n: int,
    sensitivity: float = 0.10,
    sparse_band_floor: int = 5,
) -> RegressionResult:
    """Wilson-score 95% CI regression detection (D396).

    Uses inline Wilson score interval (z=1.96, no scipy).
    Returns ``regression_detected=False`` with ``reason="sparse"`` when
    ``recent_n < sparse_band_floor``.
    """
    if recent_n < sparse_band_floor:
        return RegressionResult(
            regression_detected=False,
            lower_ci=0.0,
            upper_ci=0.0,
            threshold=max(historical_rate - sensitivity, 0.0),
            reason="sparse",
        )

    z = 1.96
    p = recent_rate
    n = recent_n

    # Wilson score interval.
    denominator = 1 + (z * z) / n
    center = (p + (z * z) / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n) / denominator

    lower_ci = max(center - margin, 0.0)
    upper_ci = min(center + margin, 1.0)

    threshold = max(historical_rate - sensitivity, 0.0)
    regression_detected = lower_ci < threshold

    return RegressionResult(
        regression_detected=regression_detected,
        lower_ci=lower_ci,
        upper_ci=upper_ci,
        threshold=threshold,
        reason="ok",
    )


def detect_trend(
    approval_rates_series: list[float],
    min_snapshots: int = 8,
) -> TrendResult:
    """Mann-Kendall trend analysis on approval rate time series (D396).

    Requires ``pymannkendall`` (already installed). Fewer than
    ``min_snapshots`` data points returns ``direction="insufficient_data"``.
    """
    if len(approval_rates_series) < min_snapshots:
        return TrendResult(
            direction="insufficient_data",
            p_value=None,
            tau=None,
        )

    import pymannkendall as mk

    result = mk.original_test(approval_rates_series)

    if result.trend == "increasing":
        direction = "increasing"
    elif result.trend == "decreasing":
        direction = "decreasing"
    else:
        direction = "no_trend"

    return TrendResult(
        direction=direction,
        p_value=result.p,
        tau=result.Tau,
    )
