"""Chunk 39 CP2 — velocity, aggregations, payload hash (D302/D303)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.change_directives.snapshot_pipeline.aggregations import (
    compute_criteria_all_satisfied,
    compute_progress_percentage,
)
from src.change_directives.snapshot_pipeline.config import SnapshotPipelineConfig
from src.change_directives.snapshot_pipeline.payload_hash import compute_payload_hash
from src.change_directives.snapshot_pipeline.velocity import (
    compute_is_stalled,
    compute_velocity,
)


def test_compute_velocity_first_snapshot_returns_none():
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 3, tzinfo=timezone.utc)
    assert compute_velocity(None, 0.5, t1, t2) is None


def test_compute_velocity_positive_per_day():
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 6, tzinfo=timezone.utc)
    # delta 0.5 over 5 days => 0.1 per day
    assert abs(compute_velocity(0.0, 0.5, t0, t1) - 0.1) < 1e-9


def test_compute_progress_percentage_mean():
    rows = [
        {"satisfied": True},
        {"satisfied": False},
        {"satisfied": True},
    ]
    assert abs(compute_progress_percentage(rows) - (2 / 3)) < 1e-9


def test_compute_criteria_all_satisfied_si_vs_oa():
    rows = [{"satisfied": True}, {"satisfied": True}]
    assert compute_criteria_all_satisfied(rows, "Strategic_Initiative") is True
    assert compute_criteria_all_satisfied(rows, "Operational_Adjustment") is None


def test_payload_hash_stable_under_key_reorder():
    a = [{"b": 2, "a": 1}]
    b = [{"a": 1, "b": 2}]
    assert compute_payload_hash(a) == compute_payload_hash(b)


def test_compute_is_stalled_requires_active_status():
    cfg = SnapshotPipelineConfig()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    assert (
        compute_is_stalled([0.0, 0.0, 0.0], "draft", old, now, cfg) is False
    )


def test_compute_is_stalled_short_series_returns_false():
    cfg = SnapshotPipelineConfig()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    assert (
        compute_is_stalled([0.001, 0.002], "active", old, now, cfg) is False
    )
