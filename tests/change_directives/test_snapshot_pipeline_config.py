"""Chunk 39 — snapshot pipeline YAML config load."""

from __future__ import annotations

from src.change_directives.snapshot_pipeline import run_snapshots
from src.change_directives.snapshot_pipeline.config import (
    SnapshotPipelineConfig,
    load_snapshot_config,
)


def test_load_snapshot_config_positive_thresholds():
    cfg = load_snapshot_config()
    assert isinstance(cfg, SnapshotPipelineConfig)
    assert cfg.velocity_window_days >= 1
    assert cfg.stalled_epsilon > 0
    assert cfg.stalled_min_age_days >= 1


def test_package_exports_run_snapshots():
    assert callable(run_snapshots)
