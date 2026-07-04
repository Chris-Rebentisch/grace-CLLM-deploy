"""Velocity and stalled-directive derivation (Chunk 39, D303)."""

from __future__ import annotations

from datetime import datetime, timezone

import pymannkendall as mk

from src.change_directives.snapshot_pipeline.config import SnapshotPipelineConfig


def compute_velocity(
    prev_progress: float | None,
    curr_progress: float,
    prev_at: datetime,
    curr_at: datetime,
) -> float | None:
    """Per-day delta in progress units; ``None`` when no prior snapshot."""
    if prev_progress is None:
        return None
    delta_p = float(curr_progress) - float(prev_progress)
    days = (curr_at - prev_at).total_seconds() / 86400.0
    denom = max(1.0, days)
    return delta_p / denom


def compute_is_stalled(
    velocity_series: list[float],
    status: str,
    status_updated_at: datetime,
    now: datetime,
    config: SnapshotPipelineConfig,
) -> bool:
    """Four-conjunct stalled check (D303)."""
    if status != "active":
        return False
    if (now - status_updated_at).total_seconds() < config.stalled_min_age_days * 86400:
        return False
    clean = [float(v) for v in velocity_series if v is not None]
    if len(clean) < 3:
        return False
    try:
        result = mk.original_test(clean, alpha=0.05)
    except Exception:
        return False
    if result.trend == "increasing" or result.p < 0.05:
        return False
    mean_v = sum(clean) / len(clean)
    if abs(mean_v) >= config.stalled_epsilon:
        return False
    return True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
